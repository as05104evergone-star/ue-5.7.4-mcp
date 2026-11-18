#include "ComboMCPGraphLayout.h"

#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "K2Node_Knot.h"
#include "Layout/SlateRect.h"

namespace ComboMCP
{
namespace
{
// PrepareAttackAssist keeps a horizontal exec row, a nearby data row and
// a separate row under the loop for Completed. Sizes are canvas units.
constexpr int32 ExecGap = 112;
constexpr int32 DataGap = 64;
constexpr int32 RowGap = 96;
constexpr int32 Padding = 32;
const FName CompletedPin(TEXT("Completed"));

FIntPoint Position(const UEdGraphNode& Node)
{
    return {Node.NodePosX, Node.NodePosY};
}

FIntRect Bounds(const UEdGraphNode& Node, const FBlueprintEditor& Editor)
{
    const FIntPoint Min = Position(Node);
    return {Min, Min + GetNodeSize(Node, &Editor)};
}

bool Overlaps(const FIntRect& A, const FIntRect& B)
{
    return A.Min.X < B.Max.X && B.Min.X < A.Max.X && A.Min.Y < B.Max.Y && B.Min.Y < A.Max.Y;
}

bool IsExec(const UEdGraphPin& Pin)
{
    return Pin.PinType.PinCategory == UEdGraphSchema_K2::PC_Exec;
}

bool HasExec(const UEdGraphNode& Node)
{
    return Node.Pins.ContainsByPredicate([](const UEdGraphPin* Pin) { return IsExec(*Pin); });
}

bool IsLowerLane(const UEdGraphPin& Output)
{
    return Output.PinName == CompletedPin || Output.PinName == TEXT("else") ||
        Output.PinName == TEXT("CastFailed");
}

void Place(UEdGraphNode& Node, FIntPoint At, const FIntPoint Size, TArray<FIntRect>& Occupied)
{
    for (;;)
    {
        const FIntRect Candidate(At - FIntPoint(16, 16), At + Size + FIntPoint(16, 16));
        const FIntRect* Collision = Occupied.FindByPredicate(
            [&Candidate](const FIntRect& Box) { return Overlaps(Box, Candidate); });
        if (!Collision)
        {
            break;
        }
        At.Y = Collision->Max.Y + RowGap;
    }
    Node.Modify();
    Node.NodePosX = At.X;
    Node.NodePosY = At.Y;
    Occupied.Emplace(At, At + Size);
}

int32 Bottom(const TArray<FIntRect>& Boxes, int32 Minimum)
{
    for (const FIntRect& Box : Boxes)
    {
        Minimum = FMath::Max(Minimum, Box.Max.Y);
    }
    return Minimum;
}

UK2Node_Knot* AddKnot(UEdGraph& Graph, FIntPoint At)
{
    FGraphNodeCreator<UK2Node_Knot> Creator(Graph);
    UK2Node_Knot* Knot = Creator.CreateNode(false);
    Knot->NodePosX = At.X;
    Knot->NodePosY = At.Y;
    Creator.Finalize();
    return Knot;
}

bool RouteThrough(UEdGraph& Graph, const UEdGraphSchema_K2& Schema, const FConnection& Link,
    FIntPoint Start, FIntPoint End, TArray<UEdGraphNode*>& Created)
{
    UK2Node_Knot* First = AddKnot(Graph, Start);
    UK2Node_Knot* Last = AddKnot(Graph, End);
    Created.Append({First, Last});
    Schema.BreakSinglePinLink(Link.Output.Resolve(), Link.Input.Resolve());
    UEdGraphPin* Output = Link.Output.Resolve();
    if (!Output || !Schema.TryCreateConnection(Output, First->GetInputPin()) ||
        !Schema.TryCreateConnection(First->GetOutputPin(), Last->GetInputPin()))
    {
        return false;
    }
    UEdGraphPin* Input = Link.Input.Resolve();
    return Input && Schema.TryCreateConnection(Last->GetOutputPin(), Input);
}

TArray<UEdGraphNode*> RouteSharedData(UEdGraph& Graph, const UEdGraphSchema_K2& Schema,
    const TArray<FConnection>& AddedLinks, const TSet<UEdGraphNode*>& NewNodes,
    const TArray<FIntRect>& Occupied, FString& Error)
{
    struct FFan
    {
        FGraphPin Source;
        TArray<FGraphPin> Targets;
    };
    TArray<FFan> Fans;
    for (const FConnection& Link : AddedLinks)
    {
        UEdGraphPin* Source = Link.Output.Resolve();
        UEdGraphPin* Target = Link.Input.Resolve();
        if (Source && Target && !IsExec(*Source) && NewNodes.Contains(Link.Input.Node) &&
            Source->LinkedTo.Contains(Target))
        {
            FFan* Fan = Fans.FindByPredicate([Source](const FFan& Candidate)
            {
                return Candidate.Source.Resolve() == Source;
            });
            if (!Fan)
            {
                Fan = &Fans.Add_GetRef({Link.Output, {}});
            }
            Fan->Targets.Add(Link.Input);
        }
    }

    TArray<UEdGraphNode*> Knots;
    int32 TrunkY = Bottom(Occupied, 0) + DataGap;
    for (const FFan& Fan : Fans)
    {
        UEdGraphPin* Source = Fan.Source.Resolve();
        if (!Source || Fan.Targets.Num() < 2 || Source->PinType.PinCategory == UEdGraphSchema_K2::PC_Wildcard ||
            Source->PinType.bIsReference)
        {
            continue;
        }
        const UEdGraphNode& Owner = *Source->GetOwningNode();
        const int32 StartX = Owner.NodePosX + GetNodeSize(Owner).X + DataGap;
        int32 EndX = MAX_int32;
        for (const FGraphPin& Input : Fan.Targets)
        {
            EndX = FMath::Min(EndX, Input.Node->NodePosX - DataGap);
        }
        if (EndX - StartX < 2 * ExecGap)
        {
            continue;
        }
        UK2Node_Knot* Start = AddKnot(Graph, {StartX, TrunkY});
        UK2Node_Knot* End = AddKnot(Graph, {EndX, TrunkY});
        Knots.Append({Start, End});
        TrunkY += DataGap;
        if (!Schema.TryCreateConnection(Source, Start->GetInputPin()) ||
            !Schema.TryCreateConnection(Start->GetOutputPin(), End->GetInputPin()))
        {
            Error = TEXT("Could not connect the shared data reroute.");
            return Knots;
        }
        for (const FGraphPin& Target : Fan.Targets)
        {
            Source = Fan.Source.Resolve();
            UEdGraphPin* Input = Target.Resolve();
            if (!Source || !Input)
            {
                Error = TEXT("Shared data pins changed while routing the connection.");
                return Knots;
            }
            Schema.BreakSinglePinLink(Source, Input);
            Input = Target.Resolve();
            if (!Input || !Schema.TryCreateConnection(End->GetOutputPin(), Input))
            {
                Error = TEXT("Could not connect a shared data reroute to its destination.");
                return Knots;
            }
        }
    }
    return Knots;
}
}

FGraphPin::FGraphPin(const UEdGraphPin& Pin)
    : Node(Pin.GetOwningNode()), Id(Pin.PinId), Name(Pin.PinName), Direction(Pin.Direction)
{
}

UEdGraphPin* FGraphPin::Resolve() const
{
    UEdGraphPin* ByName = nullptr;
    bool bAmbiguous = false;
    // Wildcard macro typing can reconstruct every pin on the node after a connection.
    for (UEdGraphPin* Pin : Node->Pins)
    {
        if (!Pin->bOrphanedPin && Pin->Direction == Direction && Pin->PinName == Name)
        {
            if (Pin->PinId == Id)
            {
                return Pin;
            }
            bAmbiguous |= ByName != nullptr;
            ByName = Pin;
        }
    }
    return bAmbiguous ? nullptr : ByName;
}

FIntPoint GetNodeSize(const UEdGraphNode& Node, const FBlueprintEditor* Editor)
{
    if (Editor)
    {
        FSlateRect Rect(0, 0, 0, 0);
        Editor->GetBoundsForNode(&Node, Rect, 0);
        if (Rect.Right > Rect.Left && Rect.Bottom > Rect.Top)
        {
            return {FMath::CeilToInt(Rect.Right - Rect.Left), FMath::CeilToInt(Rect.Bottom - Rect.Top)};
        }
    }
    if (Node.NodeWidth > 0 && Node.NodeHeight > 0)
    {
        return {Node.NodeWidth, Node.NodeHeight};
    }
    if (Node.IsA<UK2Node_Knot>())
    {
        return {32, 24};
    }
    int32 Inputs = 0;
    int32 Outputs = 0;
    for (const UEdGraphPin* Pin : Node.Pins)
    {
        if (!Pin->bHidden && !Pin->bOrphanedPin)
        {
            (Pin->Direction == EGPD_Input ? Inputs : Outputs)++;
        }
    }
    const int32 TitleWidth = Node.GetNodeTitle(ENodeTitleType::FullTitle).ToString().Len() * 10 + 72;
    return {FMath::Max(192, TitleWidth), 52 + FMath::Max(Inputs, Outputs) * 28};
}

UEdGraphNode_Comment* AddComment(UEdGraph& Graph, const FCommentGroup& Group,
    const FBlueprintEditor& Editor)
{
    FIntRect Box = Bounds(*Group.Nodes[0], Editor);
    for (const UEdGraphNode* Node : Group.Nodes)
    {
        const FIntRect Area = Bounds(*Node, Editor);
        Box.Min = Box.Min.ComponentMin(Area.Min);
        Box.Max = Box.Max.ComponentMax(Area.Max);
    }
    Box.Min -= FIntPoint(Padding, 2 * Padding);
    Box.Max += FIntPoint(Padding, Padding);
    FGraphNodeCreator<UEdGraphNode_Comment> Creator(Graph);
    UEdGraphNode_Comment* Comment = Creator.CreateNode(false);
    Creator.Finalize();
    Comment->NodePosX = Box.Min.X;
    Comment->NodePosY = Box.Min.Y;
    Comment->NodeComment = Group.Title;
    Comment->CommentColor = FLinearColor(0.15f, 0.15f, 0.15f, 0.5f);
    Comment->bCommentBubbleVisible_InDetailsPanel = false;
    Comment->bCommentBubblePinned = false;
    Comment->bCommentBubbleVisible = false;
    Comment->ResizeNode(FVector2f(Box.Width(), Box.Height()));
    for (UEdGraphNode* Node : Group.Nodes)
    {
        Comment->AddNodeUnderComment(Node);
    }
    return Comment;
}

TArray<UEdGraphNode*> ArrangeNodes(UEdGraph& Graph, const FBlueprintEditor& Editor,
    const UEdGraphSchema_K2& Schema, const TArray<UEdGraphNode*>& Movable,
    const TSet<UEdGraphNode*>& NewNodes, const TArray<FCommentGroup>& Groups,
    const TArray<FConnection>& AddedLinks, bool bRouteData, FString& Error)
{
    TSet<UEdGraphNode*> Unplaced(Movable);
    TArray<FIntRect> Occupied;
    for (const UEdGraphNode* Node : Graph.Nodes)
    {
        if (!Unplaced.Contains(Node))
        {
            Occupied.Add(Bounds(*Node, Editor));
        }
    }

    // Follow the connected exec predecessors. A second pass places Completed and
    // secondary branches below the work already placed on the first row.
    auto PlaceExec = [&](bool bLower)
    {
        bool bProgress;
        do
        {
            bProgress = false;
            for (UEdGraphNode* Node : Movable)
            {
                if (!Unplaced.Contains(Node) || !HasExec(*Node))
                {
                    continue;
                }
                UEdGraphPin* Previous = nullptr;
                bool bWaiting = false;
                for (const UEdGraphPin* Pin : Node->Pins)
                {
                    if (Pin->Direction != EGPD_Input || !IsExec(*Pin))
                    {
                        continue;
                    }
                    for (UEdGraphPin* Link : Pin->LinkedTo)
                    {
                        bWaiting |= Unplaced.Contains(Link->GetOwningNode());
                        if (!Previous || IsLowerLane(*Previous))
                        {
                            Previous = Link;
                        }
                    }
                }
                if (bWaiting || (!bLower && Previous && IsLowerLane(*Previous)))
                {
                    continue;
                }
                FIntPoint At = NewNodes.Contains(Node) ? FIntPoint(0, Bottom(Occupied, -RowGap) + RowGap)
                                                      : Position(*Node);
                if (Previous)
                {
                    UEdGraphNode* Anchor = Previous->GetOwningNode();
                    At = Position(*Anchor);
                    At.X += GetNodeSize(*Anchor, &Editor).X + ExecGap;
                    const UEdGraphPin* Completed = Anchor->FindPin(CompletedPin, EGPD_Output);
                    if (Completed && !Completed->LinkedTo.IsEmpty() && Previous != Completed)
                    {
                        At.X += 2 * ExecGap;
                    }
                    if (IsLowerLane(*Previous))
                    {
                        At.Y = Bottom(Occupied, At.Y) + RowGap;
                    }
                }
                Place(*Node, At, GetNodeSize(*Node, &Editor), Occupied);
                Unplaced.Remove(Node);
                bProgress = true;
            }
        }
        while (bProgress);
    };

    PlaceExec(false);
    PlaceExec(true);

    // Process pure dependencies from consumers back to producers.
    bool bProgress;
    do
    {
        bProgress = false;
        for (UEdGraphNode* Node : Movable)
        {
            if (!Unplaced.Contains(Node) || HasExec(*Node))
            {
                continue;
            }
            const UEdGraphNode* Consumer = nullptr;
            for (const UEdGraphPin* Pin : Node->Pins)
            {
                if (Pin->Direction == EGPD_Output)
                {
                    for (const UEdGraphPin* Link : Pin->LinkedTo)
                    {
                        const UEdGraphNode* Target = Link->GetOwningNode();
                        if (!Unplaced.Contains(Target) && (!Consumer || Target->NodePosX < Consumer->NodePosX))
                        {
                            Consumer = Target;
                        }
                    }
                }
            }
            if (Consumer)
            {
                const FIntPoint Size = GetNodeSize(*Node, &Editor);
                FIntPoint At = Position(*Consumer) + FIntPoint(-Size.X - DataGap, 88);
                for (const UEdGraphPin* Pin : Node->Pins)
                {
                    if (Pin->Direction != EGPD_Input)
                    {
                        continue;
                    }
                    for (const UEdGraphPin* Link : Pin->LinkedTo)
                    {
                        const UEdGraphNode& Source = *Link->GetOwningNode();
                        const UEdGraphPin* Completed = Source.FindPin(CompletedPin, EGPD_Output);
                        if (Completed && !Completed->LinkedTo.IsEmpty() && !Unplaced.Contains(Link->GetOwningNode()))
                        {
                            const int32 AfterLoop = Bounds(Source, Editor).Max.X + DataGap + Padding;
                            At.X = FMath::Max(At.X, AfterLoop);
                            if (At.X + Size.X + Padding <= Consumer->NodePosX)
                            {
                                // The loop element supplies the body above the Completed turn.
                                At.Y = Source.NodePosY + 48;
                            }
                        }
                    }
                }
                Place(*Node, At, Size, Occupied);
                Unplaced.Remove(Node);
                bProgress = true;
            }
        }
    }
    while (bProgress);

    for (UEdGraphNode* Node : Movable)
    {
        if (Unplaced.Contains(Node))
        {
            const FIntPoint At = NewNodes.Contains(Node) ? FIntPoint(0, Bottom(Occupied, 0) + RowGap)
                                                        : Position(*Node);
            Place(*Node, At, GetNodeSize(*Node, &Editor), Occupied);
        }
    }

    TArray<UEdGraphNode*> Created;
    for (const FConnection& Link : AddedLinks)
    {
        if (Link.Output.Name != CompletedPin || !Movable.Contains(Link.Input.Node))
        {
            continue;
        }
        const UEdGraphPin* Output = Link.Output.Resolve();
        const UEdGraphPin* Input = Link.Input.Resolve();
        if (!Output || !Input || !IsExec(*Output) || !Output->LinkedTo.Contains(Input))
        {
            continue;
        }
        const FIntRect From = Bounds(*Link.Output.Node, Editor);
        const FIntRect To = Bounds(*Link.Input.Node, Editor);
        const int32 X = From.Max.X + Padding;
        const FIntPoint Start(X, From.Max.Y - 24);
        const FIntPoint End(X + 16, To.Min.Y + 28);
        const FIntRect Route(Start, End + FIntPoint(32, 24));
        if (To.Min.X >= End.X + 32 && End.Y > Start.Y + 48 &&
            !Occupied.ContainsByPredicate([&Route](const FIntRect& Box) { return Overlaps(Box, Route); }))
        {
            if (!RouteThrough(Graph, Schema, Link, Start, End, Created))
            {
                Error = TEXT("Could not connect the loop Completed reroute.");
                return Created;
            }
        }
    }
    if (bRouteData)
    {
        Created.Append(RouteSharedData(Graph, Schema, AddedLinks, NewNodes, Occupied, Error));
    }
    if (Error.IsEmpty())
    {
        for (const FCommentGroup& Group : Groups)
        {
            Created.Add(AddComment(Graph, Group, Editor));
        }
    }
    return Created;
}

TSharedRef<FJsonObject> InspectLayout(const UEdGraph& Graph, const FBlueprintEditor& Editor,
    const TSet<UEdGraphNode*>& Scope)
{
    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    TArray<TSharedPtr<FJsonValue>> OverlappingNodes;
    TArray<TSharedPtr<FJsonValue>> BackwardExec;
    TArray<TSharedPtr<FJsonValue>> ObstructedLinks;
    TMap<const UEdGraphNode*, FIntRect> Boxes;
    for (const UEdGraphNode* Node : Graph.Nodes)
    {
        if (!Node->IsA<UEdGraphNode_Comment>())
        {
            Boxes.Add(Node, Bounds(*Node, Editor));
        }
    }
    for (int32 Index = 0; Index < Graph.Nodes.Num(); ++Index)
    {
        UEdGraphNode* A = Graph.Nodes[Index];
        if (!Boxes.Contains(A))
        {
            continue;
        }
        for (int32 Other = Index + 1; Other < Graph.Nodes.Num() && OverlappingNodes.Num() < 50; ++Other)
        {
            UEdGraphNode* B = Graph.Nodes[Other];
            if ((Scope.IsEmpty() || Scope.Contains(A) || Scope.Contains(B)) &&
                Boxes.Contains(B) && Overlaps(Boxes[A], Boxes[B]))
            {
                OverlappingNodes.Add(MakeShared<FJsonValueString>(A->GetName() + TEXT(" / ") + B->GetName()));
            }
        }
        for (const UEdGraphPin* Pin : A->Pins)
        {
            if (Pin->Direction != EGPD_Output)
            {
                continue;
            }
            for (const UEdGraphPin* Link : Pin->LinkedTo)
            {
                UEdGraphNode* B = Link->GetOwningNode();
                if (!Boxes.Contains(B) || (!Scope.IsEmpty() && !Scope.Contains(A) && !Scope.Contains(B)))
                {
                    continue;
                }
                const FString Label = A->GetName() + TEXT(".") + Pin->PinName.ToString() +
                    TEXT(" -> ") + B->GetName() + TEXT(".") + Link->PinName.ToString();
                if (IsExec(*Pin) && B->NodePosX < A->NodePosX && BackwardExec.Num() < 50)
                {
                    BackwardExec.Add(MakeShared<FJsonValueString>(Label));
                }
                // A straight-line probe between node edges is a cue for canvas review.
                const FIntRect& From = Boxes[A];
                const FIntRect& To = Boxes[B];
                const FVector2D Start(From.Max.X, From.Min.Y + From.Height() / 2);
                const FVector2D End(To.Min.X, To.Min.Y + To.Height() / 2);
                for (const auto& [Node, Box] : Boxes)
                {
                    if (Node == A || Node == B || ObstructedLinks.Num() >= 50)
                    {
                        continue;
                    }
                    const FBox2D TestBox(FVector2D(Box.Min), FVector2D(Box.Max));
                    if (FMath::LineBoxIntersection(FBox(FVector(TestBox.Min, -1), FVector(TestBox.Max, 1)),
                        FVector(Start, 0), FVector(End, 0), FVector(End - Start, 0)))
                    {
                        ObstructedLinks.Add(MakeShared<FJsonValueString>(Label + TEXT(" / ") + Node->GetName()));
                        break;
                    }
                }
            }
        }
    }
    Result->SetArrayField(TEXT("overlaps"), OverlappingNodes);
    Result->SetArrayField(TEXT("backward_exec"), BackwardExec);
    Result->SetArrayField(TEXT("possible_wire_obstructions"), ObstructedLinks);
    return Result;
}
}
