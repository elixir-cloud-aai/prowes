#!/usr/bin/env cwl-runner
# Exits with a status other than 0 (a run that fails)
cwlVersion: v1.0
class: CommandLineTool
baseCommand: [sh, -c, "exit 3"]
inputs: []
outputs: []
