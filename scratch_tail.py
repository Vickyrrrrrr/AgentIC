import os
with open("/home/vickynishad/AgentIC/designs/rv32_micro_soc/rv32_micro_soc.log", "r") as f:
    lines = f.readlines()
    for line in lines[-100:]:
        print(line, end="")
