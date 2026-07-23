---
description: Open the Sketch2AI whiteboard, draw, and pull the drawing into this session
---

<!-- Copy this file to ~/.claude/commands/sketch.md (or a project's
     .claude/commands/) and replace the path below with where you cloned the repo. -->

The user wants to draw something and hand the drawing to you. Use the Sketch2AI
bridge to capture it.

1. Run the bridge in the foreground and wait for it to finish. It blocks until
   the user sends a drawing (or times out after ~9.5 min), so pass a Bash
   `timeout` of 600000 ms:

   `uv run python3 /ABSOLUTE/PATH/TO/Sketch2AI/tools/sketch-bridge.py`

   It opens the whiteboard in the browser and, on success, prints the path to a
   PNG as its last stdout line. (Use plain `python3` if you do not use uv.)

2. If it exits non-zero (timeout or error), tell the user the sketch was not
   captured and stop — do not retry automatically.

3. On success, Read the PNG path from the last stdout line so you can see the
   drawing, then act on whatever the user asked. If they gave no other
   instruction, briefly say what you see and ask what they want done with it.

$ARGUMENTS
