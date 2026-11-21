#!/usr/bin/env cwl-runner
# Writes a message to its standard output, which becomes the output file
# `greeting` (a run that completes)
cwlVersion: v1.0
class: CommandLineTool
baseCommand: echo
inputs:
  message:
    type: string
    inputBinding:
      position: 1
stdout: greeting.txt
outputs:
  greeting:
    type: stdout
