#include "ComboMCPBlueprintEditorLibrary.h"

#include "BlueprintEditor.h"
#include "ComboMCPGraphLayout.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_CallFunction.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Knot.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Modules/ModuleManager.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Subsystems/EditorAssetSubsystem.h"
#include "Editor/Transactor.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, ComboMCPEditor)

namespace ComboMCP
{
namespace
{
using FJson = TSharedRef<FJsonObject>;
using FValues = TArray<TSharedPtr<FJsonValue>>;
constexpr TCHAR TransactionContext[] = TEXT("ComboMCP.GraphPatch");

FString Field(const FJsonObject& Object, const TCHAR* Key)
{
    FString Value;
    Object.TryGetStringField(Key, Value);
    return Value;
}

bool Flag(const FJsonObject& Object, const TCHAR* Key)
{
    bool bValue = false;
    Object.TryGetBoolField(Key, bValue);
    return bValue;
}

const FValues& Array(const FJsonObject& Object, const TCHAR* Key)
{
    static const FValues Empty;
    const FValues* Items = nullptr;
    return Object.TryGetArrayField(Key, Items) ? *Items : Empty;
}

FString ToJson(const FJson& Object)
{
    FString Text;
    FJsonSerializer::Serialize(Object, TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    return Text;
}

FJson Error(const FString& Message)
{
    FJson Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("error"), Message);
    return Result;
}

FString Guid(const FGuid& Value)
{
    return Value.ToString(EGuidFormats::Digits);
}

FString FullPath(const FString& Path)
{
    FString Full = FPaths::ConvertRelativePathToFull(Path);
    FPaths::NormalizeFilename(Full);
    FPaths::CollapseRelativeDirectories(Full);
    return Full;
}

struct FTarget
{
    UBlueprint* Blueprint = nullptr;
    UEdGraph* Graph = nullptr;
    FBlueprintEditor* Editor = nullptr;
};

FTarget FindTarget(const FJsonObject& Request)
{
    const FString Asset = Field(Request, TEXT("asset_path"));
    UAssetEditorSubsystem* Assets = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
    FTarget Found;
    double LastActivation = -1.0;
    for (UObject* Object : Assets->GetAllEditedAssets())
    {
        UBlueprint* Blueprint = Cast<UBlueprint>(Object);
        if (!Blueprint || (!Asset.IsEmpty() && Blueprint->GetPathName() != Asset &&
            Blueprint->GetOutermost()->GetName() != Asset))
        {
            continue;
        }
        IAssetEditorInstance* Instance = Assets->FindEditorForAsset(Blueprint, false);
        if (!Instance || Instance->GetEditorName() != TEXT("BlueprintEditor") ||
            Instance->GetLastActivationTime() < LastActivation)
        {
            continue;
        }
        auto* Editor = static_cast<FBlueprintEditor*>(Instance);
        Found = {Blueprint, Editor->GetFocusedGraph(), Editor};
        LastActivation = Instance->GetLastActivationTime();
    }
    return Found;
}

FJson PinSnapshot(const UEdGraphPin& Pin)
{
    FJson Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("id"), Guid(Pin.PinId));
    Json->SetStringField(TEXT("name"), Pin.PinName.ToString());
    Json->SetStringField(TEXT("direction"), Pin.Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
    Json->SetStringField(TEXT("category"), Pin.PinType.PinCategory.ToString());
    Json->SetStringField(TEXT("subcategory"), Pin.PinType.PinSubCategory.ToString());
    Json->SetStringField(TEXT("type_object"), GetPathNameSafe(Pin.PinType.PinSubCategoryObject.Get()));
    Json->SetNumberField(TEXT("container"), static_cast<int32>(Pin.PinType.ContainerType));
    Json->SetBoolField(TEXT("reference"), Pin.PinType.bIsReference);
    Json->SetStringField(TEXT("default"), Pin.GetDefaultAsString());
    Json->SetStringField(TEXT("label"), Pin.PinFriendlyName.ToString());
    Json->SetBoolField(TEXT("hidden"), Pin.bHidden);
    Json->SetBoolField(TEXT("orphaned"), Pin.bOrphanedPin);
    FValues Links;
    for (const UEdGraphPin* Peer : Pin.LinkedTo)
    {
        FJson Link = MakeShared<FJsonObject>();
        Link->SetStringField(TEXT("node"), Guid(Peer->GetOwningNode()->NodeGuid));
        Link->SetStringField(TEXT("pin"), Guid(Peer->PinId));
        Links.Add(MakeShared<FJsonValueObject>(Link));
    }
    Json->SetArrayField(TEXT("links"), Links);
    return Json;
}

FJson NodeSnapshot(const UEdGraphNode& Node)
{
    FJson Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("id"), Guid(Node.NodeGuid));
    Json->SetStringField(TEXT("name"), Node.GetName());
    Json->SetStringField(TEXT("class"), Node.GetClass()->GetName());
    Json->SetStringField(TEXT("title"), Node.GetNodeTitle(ENodeTitleType::ListView).ToString());
    Json->SetNumberField(TEXT("x"), Node.NodePosX);
    Json->SetNumberField(TEXT("y"), Node.NodePosY);
    Json->SetStringField(TEXT("comment"), Node.NodeComment);
    if (const auto* Comment = Cast<UEdGraphNode_Comment>(&Node))
    {
        FJson Style = MakeShared<FJsonObject>();
        Style->SetNumberField(TEXT("width"), Comment->NodeWidth);
        Style->SetNumberField(TEXT("height"), Comment->NodeHeight);
        Style->SetStringField(TEXT("color"), Comment->CommentColor.ToString());
        Json->SetObjectField(TEXT("comment_style"), Style);
    }
    FValues Pins;
    for (const UEdGraphPin* Pin : Node.Pins)
    {
        Pins.Add(MakeShared<FJsonValueObject>(PinSnapshot(*Pin)));
    }
    Json->SetArrayField(TEXT("pins"), Pins);
    return Json;
}

FValues SnapshotNodes(const UEdGraph& Graph)
{
    FValues Nodes;
    for (const UEdGraphNode* Node : Graph.Nodes)
    {
        Nodes.Add(MakeShared<FJsonValueObject>(NodeSnapshot(*Node)));
    }
    return Nodes;
}

FString Revision(const FTarget& Target, const FValues& Nodes)
{
    static const FGuid Session = FGuid::NewGuid();
    FJson State = MakeShared<FJsonObject>();
    State->SetStringField(TEXT("session"), Guid(Session));
    State->SetStringField(TEXT("graph"), Target.Graph->GetPathName());
    State->SetStringField(TEXT("graph_id"), Guid(Target.Graph->GraphGuid));
    State->SetArrayField(TEXT("nodes"), Nodes);
    const FTCHARToUTF8 Bytes(*ToJson(State));
    FMD5 Hash;
    Hash.Update(reinterpret_cast<const uint8*>(Bytes.Get()), Bytes.Length());
    uint8 Digest[16];
    Hash.Final(Digest);
    return BytesToHex(Digest, UE_ARRAY_COUNT(Digest));
}

FJson Summary(const FTarget& Target, const FString& CurrentRevision)
{
    FJson Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("asset_path"), Target.Blueprint->GetPathName());
    Result->SetStringField(TEXT("graph"), Target.Graph->GetName());
    Result->SetStringField(TEXT("graph_id"), Guid(Target.Graph->GraphGuid));
    Result->SetStringField(TEXT("revision"), CurrentRevision);
    Result->SetBoolField(TEXT("dirty"), Target.Blueprint->GetOutermost()->IsDirty());
    Result->SetBoolField(TEXT("compiled"), Target.Blueprint->IsUpToDate());
    Result->SetNumberField(TEXT("node_count"), Target.Graph->Nodes.Num());
    return Result;
}

FValues SavedGraphManifest(const UEdGraph& Graph)
{
    FValues Nodes;
    for (const UEdGraphNode* Node : Graph.Nodes)
    {
        FJson Record = MakeShared<FJsonObject>();
        Record->SetStringField(TEXT("id"), Guid(Node->NodeGuid));
        Record->SetNumberField(TEXT("x"), Node->NodePosX);
        Record->SetNumberField(TEXT("y"), Node->NodePosY);
        if (Node->IsA<UEdGraphNode_Comment>())
        {
            Record->SetStringField(TEXT("comment"), Node->NodeComment);
        }
        FValues Links;
        for (const UEdGraphPin* Pin : Node->Pins)
        {
            if (Pin->Direction != EGPD_Output)
            {
                continue;
            }
            for (const UEdGraphPin* Peer : Pin->LinkedTo)
            {
                Links.Add(MakeShared<FJsonValueString>(Pin->PinName.ToString() + TEXT("|") +
                    Guid(Peer->GetOwningNode()->NodeGuid) + TEXT("|") + Peer->PinName.ToString()));
            }
        }
        Record->SetArrayField(TEXT("links"), Links);
        Nodes.Add(MakeShared<FJsonValueObject>(Record));
    }
    return Nodes;
}

TMap<FString, UEdGraphNode*> GraphNodes(const UEdGraph& Graph)
{
    TMap<FString, UEdGraphNode*> Nodes;
    for (UEdGraphNode* Node : Graph.Nodes)
    {
        Nodes.Add(Guid(Node->NodeGuid), Node);
    }
    return Nodes;
}

template<typename T, typename Initializer>
T* AddNode(UEdGraph& Graph, Initializer Initialize)
{
    FGraphNodeCreator<T> Creator(Graph);
    T* Node = Creator.CreateNode(false);
    Initialize(*Node);
    Creator.Finalize();
    return Node;
}

template<typename T>
T* AddNode(UEdGraph& Graph)
{
    return AddNode<T>(Graph, [](T&) {});
}

template<typename T>
T* AddVariable(const FTarget& Target, const FJsonObject& Spec, FString& Failure)
{
    const FName Name(*Field(Spec, TEXT("member")));
    const FString OwnerPath = Field(Spec, TEXT("owner"));
    UClass* Class = OwnerPath.IsEmpty() ? Target.Blueprint->SkeletonGeneratedClass.Get()
        : LoadObject<UClass>(nullptr, *OwnerPath);
    if (Flag(Spec, TEXT("local")))
    {
        if (const FBPVariableDescription* Description =
            FBlueprintEditorUtils::FindLocalVariable(Target.Blueprint, Target.Graph, Name))
        {
            return AddNode<T>(*Target.Graph, [&](T& Node)
            {
                Node.VariableReference.SetLocalMember(Name, Target.Graph->GetName(), Description->VarGuid);
            });
        }
    }
    else if (const FProperty* Property = Class ? FindFProperty<FProperty>(Class, Name) : nullptr)
    {
        const bool bReadOnly = T::StaticClass() == UK2Node_VariableSet::StaticClass() &&
            Property->HasAnyPropertyFlags(CPF_BlueprintReadOnly);
        if (Property->HasAnyPropertyFlags(CPF_BlueprintVisible) && !bReadOnly)
        {
            return AddNode<T>(*Target.Graph, [&](T& Node)
            {
                if (OwnerPath.IsEmpty())
                {
                    Node.VariableReference.SetSelfMember(Name);
                }
                else
                {
                    Node.VariableReference.SetExternalMember(Name, Class);
                }
            });
        }
    }
    Failure = FString::Printf(TEXT("Blueprint variable is unavailable: %s"), *Name.ToString());
    return nullptr;
}

UEdGraphNode* CreateNode(const FTarget& Target, const FJsonObject& Spec, FString& Failure)
{
    UEdGraph& Graph = *Target.Graph;
    const FString Kind = Field(Spec, TEXT("kind"));
    const FName Member(*Field(Spec, TEXT("member")));
    const FString Owner = Field(Spec, TEXT("owner"));

    if (Kind == TEXT("branch")) return AddNode<UK2Node_IfThenElse>(Graph);
    if (Kind == TEXT("sequence")) return AddNode<UK2Node_ExecutionSequence>(Graph);
    if (Kind == TEXT("reroute")) return AddNode<UK2Node_Knot>(Graph);
    if (Kind == TEXT("get")) return AddVariable<UK2Node_VariableGet>(Target, Spec, Failure);
    if (Kind == TEXT("set")) return AddVariable<UK2Node_VariableSet>(Target, Spec, Failure);
    if (Kind == TEXT("call"))
    {
        UClass* Class = Owner.IsEmpty() ? Target.Blueprint->SkeletonGeneratedClass.Get()
            : LoadObject<UClass>(nullptr, *Owner);
        UFunction* Function = Class ? Class->FindFunctionByName(Member) : nullptr;
        if (Function && UEdGraphSchema_K2::CanUserKismetCallFunction(Function) &&
            Function->GetOuterUClass() != UComboMCPBlueprintEditorLibrary::StaticClass())
        {
            return AddNode<UK2Node_CallFunction>(Graph, [&](UK2Node_CallFunction& Node)
            {
                Node.SetFromFunction(Function);
            });
        }
    }
    if (Kind == TEXT("cast"))
    {
        if (UClass* Class = LoadObject<UClass>(nullptr, *Owner))
        {
            return AddNode<UK2Node_DynamicCast>(Graph, [&](UK2Node_DynamicCast& Node)
            {
                Node.TargetType = Class;
            });
        }
    }
    if (Kind == TEXT("macro"))
    {
        UBlueprint* Library = Owner.IsEmpty() ? Target.Blueprint : LoadObject<UBlueprint>(nullptr, *Owner);
        if (Library)
        {
            for (UEdGraph* Macro : Library->MacroGraphs)
            {
                if (Macro->GetFName() == Member && Macro != &Graph &&
                    (Library == Target.Blueprint || Library->BlueprintType == BPTYPE_MacroLibrary))
                {
                    auto* Node = AddNode<UK2Node_MacroInstance>(Graph,
                        [Macro](UK2Node_MacroInstance& Instance) { Instance.SetMacroGraph(Macro); });
                    if (Node->CanPasteHere(&Graph))
                    {
                        return Node;
                    }
                    break;
                }
            }
        }
    }
    Failure = FString::Printf(TEXT("Cannot create node kind '%s' for %s %s in this graph."),
        *Kind, *Owner, *Member.ToString());
    return nullptr;
}

UEdGraphPin* FindPin(const TMap<FString, UEdGraphNode*>& Nodes, const FString& NodeId,
    const FString& PinId, EEdGraphPinDirection Direction)
{
    UEdGraphNode* Node = Nodes.FindRef(NodeId);
    if (!Node || PinId.IsEmpty())
    {
        return nullptr;
    }
    FGuid Parsed;
    const bool bGuid = FGuid::ParseExact(PinId, EGuidFormats::Digits, Parsed);
    UEdGraphPin* Found = nullptr;
    for (UEdGraphPin* Pin : Node->Pins)
    {
        if (!Pin->bOrphanedPin && Pin->Direction == Direction &&
            (bGuid ? Pin->PinId == Parsed : Pin->PinName.ToString() == PinId))
        {
            if (Found)
            {
                return nullptr;
            }
            Found = Pin;
        }
    }
    return Found;
}

bool ReadPosition(const FJsonObject& Spec, FIntPoint& Position)
{
    int32 X = 0;
    int32 Y = 0;
    if (!Spec.TryGetNumberField(TEXT("x"), X) || !Spec.TryGetNumberField(TEXT("y"), Y) ||
        X < -1000000 || X > 1000000 || Y < -1000000 || Y > 1000000)
    {
        return false;
    }
    Position = {X, Y};
    return true;
}

struct FPatchResult
{
    TMap<FString, FGuid> Added;
    TSet<UEdGraphNode*> Touched;
    FString Failure;
    FValues CompilerMessages;
    int32 Errors = 0;
    int32 Warnings = 0;
};

void ApplyPatch(const FTarget& Target, const FJsonObject& Request, FPatchResult& Result)
{
    UEdGraph& Graph = *Target.Graph;
    const auto& Schema = *CastChecked<UEdGraphSchema_K2>(Graph.GetSchema());
    TMap<FString, UEdGraphNode*> Nodes = GraphNodes(Graph);
    TSet<UEdGraphNode*> NewNodes;
    TArray<UEdGraphNode*> Movable;
    TSet<UEdGraphNode*> Positioned;
    TArray<FConnection> AddedLinks;
    TArray<FCommentGroup> Groups;

    for (const auto& Value : Array(Request, TEXT("nodes")))
    {
        const auto& Spec = *Value->AsObject();
        const FString Id = Field(Spec, TEXT("id"));
        if (!Id.StartsWith(TEXT("$")) || Id.Len() < 2 || Nodes.Contains(Id))
        {
            Result.Failure = TEXT("New node IDs must be unique aliases beginning with '$'.");
            return;
        }
        UEdGraphNode* Node = CreateNode(Target, Spec, Result.Failure);
        if (!Node)
        {
            return;
        }
        Nodes.Add(Id, Node);
        NewNodes.Add(Node);
        Result.Touched.Add(Node);
        Result.Added.Add(Id, Node->NodeGuid);
        if (Spec.HasField(TEXT("x")) || Spec.HasField(TEXT("y")))
        {
            FIntPoint Position;
            if (!ReadPosition(Spec, Position))
            {
                Result.Failure = TEXT("An explicit node position needs both x and y.");
                return;
            }
            Node->NodePosX = Position.X;
            Node->NodePosY = Position.Y;
            Positioned.Add(Node);
        }
        else
        {
            Movable.Add(Node);
        }
    }

    for (const auto& Value : Array(Request, TEXT("moves")))
    {
        const auto& Spec = *Value->AsObject();
        UEdGraphNode* Node = Nodes.FindRef(Field(Spec, TEXT("node")));
        FIntPoint Position;
        if (!Node || !ReadPosition(Spec, Position))
        {
            Result.Failure = TEXT("Each move needs a node from this graph and both x and y.");
            return;
        }
        Node->Modify();
        Node->NodePosX = Position.X;
        Node->NodePosY = Position.Y;
        Positioned.Add(Node);
        Movable.Remove(Node);
        Result.Touched.Add(Node);
    }

    for (const auto& Value : Array(Request, TEXT("arrange")))
    {
        UEdGraphNode* Node = Nodes.FindRef(Value->AsString());
        if (!Node || Node->IsA<UEdGraphNode_Comment>() || Positioned.Contains(Node))
        {
            Result.Failure = TEXT("Each node to arrange must be in this graph and have no explicit position.");
            return;
        }
        Movable.AddUnique(Node);
        Result.Touched.Add(Node);
    }

    for (const auto& Value : Array(Request, TEXT("comments")))
    {
        const auto& Spec = *Value->AsObject();
        FCommentGroup Group;
        Group.Title = Field(Spec, TEXT("title")).TrimStartAndEnd();
        const FValues& Members = Array(Spec, TEXT("nodes"));
        for (const auto& Id : Members)
        {
            FString Member;
            UEdGraphNode* Node = Id->TryGetString(Member) ? Nodes.FindRef(Member) : nullptr;
            if (!Node || Node->IsA<UEdGraphNode_Comment>())
            {
                Result.Failure = TEXT("Comment groups require graph nodes.");
                return;
            }
            Group.Nodes.AddUnique(Node);
            Result.Touched.Add(Node);
        }
        if (Group.Title.IsEmpty() || Group.Title.Len() > 100 || Group.Nodes.IsEmpty())
        {
            Result.Failure = TEXT("A comment group needs a short title and at least one node.");
            return;
        }
        Groups.Add(MoveTemp(Group));
    }

    for (const TCHAR* FieldName : {TEXT("disconnect"), TEXT("connect")})
    {
        const bool bConnect = FCString::Strcmp(FieldName, TEXT("connect")) == 0;
        for (const auto& Value : Array(Request, FieldName))
        {
            const auto& Spec = *Value->AsObject();
            UEdGraphPin* Output = FindPin(Nodes, Field(Spec, TEXT("from")), Field(Spec, TEXT("out")), EGPD_Output);
            UEdGraphPin* Input = FindPin(Nodes, Field(Spec, TEXT("to")), Field(Spec, TEXT("in")), EGPD_Input);
            if (!Output || !Input || Output->GetOwningNode() == Input->GetOwningNode())
            {
                Result.Failure = FString::Printf(TEXT("Cannot resolve pins for %s: %s"), FieldName, *ToJson(Value->AsObject().ToSharedRef()));
                return;
            }
            Result.Touched.Add(Output->GetOwningNode());
            Result.Touched.Add(Input->GetOwningNode());
            const bool bLinked = Output->LinkedTo.Contains(Input);
            if (bConnect == bLinked)
            {
                continue;
            }
            Output->Modify();
            Input->Modify();
            if (!bConnect)
            {
                Schema.BreakSinglePinLink(Output, Input);
                continue;
            }
            const FPinConnectionResponse Response = Schema.CanCreateConnection(Output, Input);
            const FConnection Link{FGraphPin(*Output), FGraphPin(*Input)};
            if (Response.Response != CONNECT_RESPONSE_MAKE || !Schema.TryCreateConnection(Output, Input))
            {
                Result.Failure = TEXT("Connection rejected. Disconnect replaced links explicitly and add the conversion node if needed. ")
                    + Response.Message.ToString();
                return;
            }
            AddedLinks.Add(Link);
        }
    }

    for (const auto& Value : Array(Request, TEXT("defaults")))
    {
        const auto& Spec = *Value->AsObject();
        UEdGraphPin* Pin = FindPin(Nodes, Field(Spec, TEXT("node")), Field(Spec, TEXT("pin")), EGPD_Input);
        if (!Pin || !Pin->LinkedTo.IsEmpty() || Pin->bDefaultValueIsReadOnly || Pin->bDefaultValueIsIgnored ||
            Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
        {
            Result.Failure = TEXT("A default needs an unconnected editable data input.");
            return;
        }
        const FString Text = Field(Spec, TEXT("value"));
        const FString ObjectPath = Field(Spec, TEXT("object"));
        UObject* Object = ObjectPath.IsEmpty() ? nullptr : LoadObject<UObject>(nullptr, *ObjectPath);
        if (!ObjectPath.IsEmpty() && !Object)
        {
            Result.Failure = TEXT("Default object could not be loaded: ") + ObjectPath;
            return;
        }
        const bool bText = Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Text;
        const FText TextValue = bText ? FText::FromString(Text) : FText::GetEmpty();
        Result.Failure = Schema.IsPinDefaultValid(Pin, bText ? FString() : Text, Object, TextValue);
        if (!Result.Failure.IsEmpty())
        {
            return;
        }
        Pin->Modify();
        Result.Touched.Add(Pin->GetOwningNode());
        if (Spec.HasField(TEXT("object")))
        {
            Schema.TrySetDefaultObject(*Pin, Object);
        }
        else if (bText)
        {
            Schema.TrySetDefaultText(*Pin, TextValue);
        }
        else
        {
            Schema.TrySetDefaultValue(*Pin, Text);
        }
    }

    bool bRouteData = true;
    Request.TryGetBoolField(TEXT("route_data"), bRouteData);
    const TArray<UEdGraphNode*> Created = ArrangeNodes(Graph, *Target.Editor, Schema, Movable,
        NewNodes, Groups, AddedLinks, bRouteData, Result.Failure);
    for (UEdGraphNode* Node : Created)
    {
        Result.Touched.Add(Node);
    }
}

FJson CommitPatch(const FTarget& Target, const FJsonObject& Request, const FString& Before)
{
    if (!Target.Editor->InEditingMode() || FBlueprintEditorUtils::IsGraphReadOnly(Target.Graph))
    {
        return Error(TEXT("The focused graph is read-only."));
    }
    if (!GEditor->CanTransact() || !GEditor->Trans || GEditor->Trans->IsActive() || GIsTransacting)
    {
        return Error(TEXT("Wait for the current editor transaction to finish."));
    }

    const bool bWasDirty = Target.Blueprint->GetOutermost()->IsDirty();
    const EBlueprintStatus PreviousStatus = Target.Blueprint->Status;
    FPatchResult Patch;
    bool bCompiled = false;
    {
        FScopedTransaction Transaction(TransactionContext,
            FText::FromString(TEXT("ComboMCP - Edit Blueprint Graph")), Target.Blueprint);
        Target.Blueprint->Modify();
        Target.Graph->Modify();
        ApplyPatch(Target, Request, Patch);
        if (Patch.Failure.IsEmpty() && Before == Revision(Target, SnapshotNodes(*Target.Graph)))
        {
            Target.Blueprint->Status = PreviousStatus;
            Target.Blueprint->GetOutermost()->SetDirtyFlag(bWasDirty);
            Transaction.Cancel();
            FJson Result = Summary(Target, Before);
            Result->SetBoolField(TEXT("changed"), false);
            return Result;
        }
        if (Patch.Failure.IsEmpty())
        {
            Target.Graph->NotifyGraphChanged();
            FBlueprintEditorUtils::MarkBlueprintAsModified(Target.Blueprint);
            FCompilerResultsLog Log;
            FKismetEditorUtilities::CompileBlueprint(Target.Blueprint, EBlueprintCompileOptions::SkipSave, &Log);
            bCompiled = true;
            Patch.Errors = Log.NumErrors;
            Patch.Warnings = Log.NumWarnings;
            for (const auto& Message : Log.Messages)
            {
                if (Message->GetSeverity() <= EMessageSeverity::Warning && Patch.CompilerMessages.Num() < 30)
                {
                    Patch.CompilerMessages.Add(MakeShared<FJsonValueString>(Message->ToText().ToString()));
                }
            }
            if (Patch.Errors || !Target.Blueprint->IsUpToDate())
            {
                Patch.Failure = TEXT("Blueprint compilation failed.");
            }
        }
    }

    if (!Patch.Failure.IsEmpty())
    {
        const bool bUndone = GEditor->UndoTransaction(false);
        if (bUndone && bCompiled && (PreviousStatus == BS_UpToDate || PreviousStatus == BS_UpToDateWithWarnings))
        {
            FKismetEditorUtilities::CompileBlueprint(Target.Blueprint, EBlueprintCompileOptions::SkipSave);
        }
        const bool bRestored = bUndone && Before == Revision(Target, SnapshotNodes(*Target.Graph));
        if (bRestored)
        {
            Target.Blueprint->Status = PreviousStatus;
            Target.Blueprint->GetOutermost()->SetDirtyFlag(bWasDirty);
        }
        FJson Result = Error(Patch.Failure);
        Result->SetBoolField(TEXT("rolled_back"), bRestored);
        Result->SetArrayField(TEXT("compiler_messages"), Patch.CompilerMessages);
        return Result;
    }

    FJson Result = Summary(Target, Revision(Target, SnapshotNodes(*Target.Graph)));
    Result->SetBoolField(TEXT("changed"), true);
    Result->SetBoolField(TEXT("dirty_before_apply"), bWasDirty);
    Result->SetNumberField(TEXT("compiler_errors"), Patch.Errors);
    Result->SetNumberField(TEXT("compiler_warnings"), Patch.Warnings);
    Result->SetArrayField(TEXT("compiler_messages"), Patch.CompilerMessages);
    FJson Added = MakeShared<FJsonObject>();
    for (const auto& [Alias, Id] : Patch.Added)
    {
        Added->SetStringField(Alias, Guid(Id));
    }
    Result->SetObjectField(TEXT("added"), Added);
    FValues Affected;
    for (UEdGraphNode* Node : Target.Graph->Nodes)
    {
        if (Patch.Touched.Contains(Node))
        {
            Affected.Add(MakeShared<FJsonValueString>(Guid(Node->NodeGuid)));
        }
    }
    Result->SetArrayField(TEXT("affected"), Affected);
    return Result;
}

bool CheckArrays(const FJsonObject& Request)
{
    for (const TCHAR* Key : {TEXT("nodes"), TEXT("moves"), TEXT("connect"), TEXT("disconnect"),
         TEXT("defaults"), TEXT("comments"), TEXT("arrange"), TEXT("scope")})
    {
        const FValues* Items = nullptr;
        if (!Request.HasField(Key))
        {
            continue;
        }
        if (!Request.TryGetArrayField(Key, Items) || Items->Num() > 200)
        {
            return false;
        }
        const bool bStrings = FCString::Strcmp(Key, TEXT("arrange")) == 0 ||
            FCString::Strcmp(Key, TEXT("scope")) == 0;
        for (const auto& Item : *Items)
        {
            if (Item->Type != (bStrings ? EJson::String : EJson::Object))
            {
                return false;
            }
        }
    }
    return true;
}

FJson Run(const FJsonObject& Request)
{
    if (!GEditor || !IsInGameThread() || IsRunningCommandlet())
    {
        return Error(TEXT("Open the Blueprint graph in Unreal Editor."));
    }
    if (!FPaths::IsSamePath(FullPath(Field(Request, TEXT("project_file"))), FullPath(FPaths::GetProjectFilePath())))
    {
        return Error(TEXT("The connected editor is running a different .uproject."));
    }
    const FString Action = Field(Request, TEXT("action"));
    const bool bWrite = Action == TEXT("apply") || Action == TEXT("save");
    if (bWrite && !Flag(Request, TEXT("authorized")))
    {
        return Error(TEXT("Blueprint editing requires an authorized call."));
    }
    if (bWrite && (GEditor->PlayWorld || GEditor->bIsSimulatingInEditor))
    {
        return Error(TEXT("Stop PIE or simulation before editing the Blueprint."));
    }
    if (!CheckArrays(Request))
    {
        return Error(TEXT("Invalid graph command array. Each array takes up to 200 entries."));
    }
    FTarget Target = FindTarget(Request);
    const FString ExpectedGraph = Field(Request, TEXT("graph"));
    if (!bWrite && Target.Editor && !ExpectedGraph.IsEmpty() && Flag(Request, TEXT("open_graph")))
    {
        TArray<UEdGraph*> Graphs;
        Target.Blueprint->GetAllGraphs(Graphs);
        UEdGraph* Match = nullptr;
        for (UEdGraph* Graph : Graphs)
        {
            if (Graph->GetName() == ExpectedGraph)
            {
                if (Match)
                {
                    return Error(TEXT("More than one graph has this name. Select the intended tab in Unreal."));
                }
                Match = Graph;
            }
        }
        if (Match)
        {
            Target.Editor->OpenGraphAndBringToFront(Match, true);
            Target.Graph = Target.Editor->GetFocusedGraph();
        }
    }
    if (!Target.Graph || !Target.Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
    {
        return Error(TEXT("Open a K2 graph tab in the Blueprint editor."));
    }
    if (!ExpectedGraph.IsEmpty() && Target.Graph->GetName() != ExpectedGraph)
    {
        return Error(TEXT("The focused graph changed. Inspect the intended tab first."));
    }

    const FValues Nodes = SnapshotNodes(*Target.Graph);
    const FString Current = Revision(Target, Nodes);
    if (bWrite)
    {
        if (Field(Request, TEXT("asset_path")).IsEmpty() || ExpectedGraph.IsEmpty() ||
            Current != Field(Request, TEXT("revision")))
        {
            return Error(TEXT("Inspect the current asset and graph again. The requested revision does not match."));
        }
        if (Action == TEXT("apply"))
        {
            return CommitPatch(Target, Request, Current);
        }
        if (!Target.Blueprint->IsUpToDate())
        {
            return Error(TEXT("The Blueprint needs a successful compile before saving."));
        }
        UEditorAssetSubsystem* Assets = GEditor->GetEditorSubsystem<UEditorAssetSubsystem>();
        if (!Assets->SaveLoadedAsset(Target.Blueprint, false))
        {
            return Error(TEXT("Unreal could not save the Blueprint package."));
        }
        FJson Result = Summary(Target, Revision(Target, SnapshotNodes(*Target.Graph)));
        Result->SetBoolField(TEXT("saved"), true);
        Result->SetArrayField(TEXT("saved_graph_manifest"), SavedGraphManifest(*Target.Graph));
        return Result;
    }
    FJson Result = Summary(Target, Current);
    if (Action == TEXT("inspect"))
    {
        TSet<FString> Scope;
        for (const auto& Id : Array(Request, TEXT("scope")))
        {
            Scope.Add(Id->AsString());
        }
        const FValues Selected = Nodes.FilterByPredicate([&Scope](const TSharedPtr<FJsonValue>& Node)
        {
            return Scope.IsEmpty() || Scope.Contains(Field(*Node->AsObject(), TEXT("id")));
        });
        int32 Offset = 0;
        int32 Limit = 30;
        Request.TryGetNumberField(TEXT("offset"), Offset);
        Request.TryGetNumberField(TEXT("limit"), Limit);
        Offset = FMath::Clamp(Offset, 0, Selected.Num());
        Limit = FMath::Clamp(Limit, 1, 100);
        const int32 Count = FMath::Min(Limit, Selected.Num() - Offset);
        FValues Page;
        if (Count > 0)
        {
            Page.Append(Selected.GetData() + Offset, Count);
        }
        Result->SetArrayField(TEXT("nodes"), Page);
        if (Offset + Count < Selected.Num())
        {
            Result->SetNumberField(TEXT("next_offset"), Offset + Count);
        }
        FValues Selection;
        for (UObject* Item : Target.Editor->GetSelectedNodes())
        {
            if (const auto* Node = Cast<UEdGraphNode>(Item))
            {
                Selection.Add(MakeShared<FJsonValueString>(Guid(Node->NodeGuid)));
            }
        }
        Result->SetArrayField(TEXT("selection"), Selection);
        return Result;
    }
    if (Action == TEXT("review"))
    {
        const auto ById = GraphNodes(*Target.Graph);
        TSet<UEdGraphNode*> Scope;
        for (const auto& Id : Array(Request, TEXT("scope")))
        {
            UEdGraphNode* Node = ById.FindRef(Id->AsString());
            if (!Node)
            {
                return Error(TEXT("Review node is absent from the focused graph."));
            }
            Scope.Add(Node);
        }
        const FString Focus = Field(Request, TEXT("focus"));
        if (!Focus.IsEmpty())
        {
            UEdGraphNode* Node = ById.FindRef(Focus);
            if (!Node)
            {
                return Error(TEXT("Focus node is absent from the focused graph."));
            }
            Target.Editor->JumpToNode(Node);
        }
        Result->SetObjectField(TEXT("layout"), InspectLayout(*Target.Graph, *Target.Editor, Scope));
        return Result;
    }
    return Error(TEXT("Supported graph actions: inspect, apply, review, save."));
}
}
}

FString UComboMCPBlueprintEditorLibrary::RunGraphCommand(const FString& RequestJson)
{
    using namespace ComboMCP;
    TSharedPtr<FJsonObject> Request;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(RequestJson), Request) || !Request)
    {
        return ToJson(ComboMCP::Error(TEXT("Invalid graph command JSON.")));
    }
    return ToJson(Run(*Request));
}
