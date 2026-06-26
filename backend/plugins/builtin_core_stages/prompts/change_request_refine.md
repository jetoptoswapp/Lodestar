You are refining an existing Implementation Brief for an EXISTING codebase.

{{WORKSPACE}}

Re-read the relevant code before refining. Output the COMPLETE updated brief (same format as the draft), incorporating the instruction. Append [BRIEF_READY] when the brief is complete.

If the current brief already uses canonical user-story sections (`## Epic N:` / `### Story N.M —`) for a large change, PRESERVE that shape — do not downgrade it back to a flat `CH-N` list (the delivery pipeline fans those stories into one issue + MR each).

--- Attached reference files ---
{{ATTACHMENTS}}
--- End of attached files ---

--- Current brief ---
{{BRIEF_DRAFT}}
--- End of current brief ---

--- Instruction ---
{{INSTRUCTION}}
--- End of instruction ---
