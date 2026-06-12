You are refining an existing UI design document.

Re-read the PRD. Keep the established design direction, font pairing and design tokens unless the instruction explicitly says otherwise. Output the COMPLETE updated design document — same format rules as the draft: `# <Project Name> — UI Design`, `## Design Direction`, `## Design Tokens` (one ```css fence with the full `:root` variables), then every key screen as `## Screen: <name>` with exactly one self-contained ```html fence (ALL screens, including unchanged ones, reproduced in full). Each HTML must stay self-contained: inline CSS/JS, Google Fonts only, full `:root` tokens repeated, realistic sample data, never three consecutive backticks inside the HTML. Append [UI_READY] at the very end when the document is complete.

--- PRD ---
{{PRD_DRAFT}}
--- End of PRD ---

--- Attached reference files ---
{{ATTACHMENTS}}
--- End of attached files ---

--- Current design ---
{{UI_DESIGN_DRAFT}}
--- End of current design ---

--- Instruction ---
{{INSTRUCTION}}
--- End of instruction ---
