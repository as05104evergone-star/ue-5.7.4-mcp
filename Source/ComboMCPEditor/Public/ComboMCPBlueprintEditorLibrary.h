#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "ComboMCPBlueprintEditorLibrary.generated.h"

UCLASS()
class UComboMCPBlueprintEditorLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "ComboMCP", meta = (DevelopmentOnly))
    static FString RunGraphCommand(const FString& RequestJson);
};
