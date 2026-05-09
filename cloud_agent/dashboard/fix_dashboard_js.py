import os

file_path = r"c:\Users\nadig\Downloads\Cloud-Agentic-AI-main\cloud_agent\dashboard\static\dashboard.js"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
in_reasoning = False
found_garbage = False

for line in lines:
    if "function renderReasoning()" in line:
        in_reasoning = True
    
    if in_reasoning and "const vols = state.volumes || [];" in line:
        # We found the break
        new_lines.append("        }\n")
        new_lines.append("    }\n")
        new_lines.append("\n")
        new_lines.append("    el.textContent = reason;\n")
        new_lines.append("\n")
        new_lines.append("    // Update Overall Confidence\n")
        new_lines.append("    const confEl = document.getElementById('ai-confidence');\n")
        new_lines.append("    if (confEl) {\n")
        new_lines.append("        const conf = state.overall_confidence || 0;\n")
        new_lines.append("        confEl.textContent = `Confidence: ${conf}%`;\n")
        new_lines.append("    }\n")
        new_lines.append("}\n")
        new_lines.append("\n")
        new_lines.append("function renderKPIs() {\n")
        new_lines.append("    const insts = state.instances || [];\n")
        new_lines.append("    const vols = state.volumes || [];\n")
        in_reasoning = False
        continue

    if in_reasoning and "function renderKPIs()" in line:
         # Already fixed or something else
         in_reasoning = False

    new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Fixed dashboard.js structure.")
