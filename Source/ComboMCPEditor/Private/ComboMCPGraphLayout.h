#pragma once

#include "CoreMinimal.h"
#include "EdGraph/EdGraphNode.h"

class FBlueprintEditor;
class UEdGraphSchema_K2;
class UEdGraphNode_Comment;
class FJsonObject;

namespace ComboMCP
{
struct FGraphPin
{
    explicit FGraphPin(const UEdGraphPin& Pin);
    UEdGraphPin* Resolve() const;

    UEdGraphNode* Node;
    FGuid Id;
    FName Name;
    EEdGraphPinDirection Direction;
};

struct FConnection
{
    FGraphPin Output;
    FGraphPin Input;
};

struct FCommentGroup
{
    FString Title;
    TArray<UEdGraphNode*> Nodes;
};

FIntPoint GetNodeSize(const UEdGraphNode& Node, const FBlueprintEditor* Editor = nullptr);
UEdGraphNode_Comment* AddComment(UEdGraph& Graph, const FCommentGroup& Group,
    const FBlueprintEditor& Editor);
TArray<UEdGraphNode*> ArrangeNodes(UEdGraph& Graph, const FBlueprintEditor& Editor,
    const UEdGraphSchema_K2& Schema, const TArray<UEdGraphNode*>& Movable,
    const TSet<UEdGraphNode*>& NewNodes, const TArray<FCommentGroup>& Groups,
    const TArray<FConnection>& AddedLinks, bool bRouteData, FString& Error);
TSharedRef<FJsonObject> InspectLayout(const UEdGraph& Graph, const FBlueprintEditor& Editor,
    const TSet<UEdGraphNode*>& Scope);
}
