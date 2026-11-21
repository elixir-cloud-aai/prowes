#!/usr/bin/env cwl-runner
# Sleeps for some seconds (a run that is canceled while it runs)
cwlVersion: v1.0
class: CommandLineTool
baseCommand: sleep
inputs:
  seconds:
    type: int
    inputBinding:
      position: 1
outputs: []
