using UnrealBuildTool;

public class ComboMCPEditor : ModuleRules
{
    public ComboMCPEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] {
            "BlueprintGraph", "GraphEditor", "Json", "Kismet", "SlateCore", "UnrealEd"
        });
    }
}
