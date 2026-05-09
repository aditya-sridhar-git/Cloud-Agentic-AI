import os

file_path = r"c:\Users\nadig\Downloads\Cloud-Agentic-AI-main\cloud_agent\dashboard\app.py"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "@app.get(\"/dashboard.js\")" in line:
        # Insert missing routes before dashboard.js
        new_lines.append("@app.get(\"/dashboard.css\")\n")
        new_lines.append("async def dashboard_style():\n")
        new_lines.append("    return FileResponse(_STATIC_DIR / \"dashboard.css\", media_type=\"text/css\")\n")
        new_lines.append("\n\n")
        new_lines.append("@app.get(\"/confidence.css\")\n")
        new_lines.append("async def confidence_style():\n")
        new_lines.append("    return FileResponse(_STATIC_DIR / \"confidence.css\", media_type=\"text/css\")\n")
        new_lines.append("\n\n")
    new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Fixed app.py structure and added confidence.css route.")
