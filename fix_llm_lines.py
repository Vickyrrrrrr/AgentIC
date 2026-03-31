path = "src/agentic/orchestrator.py"
with open(path) as f:
    lines = f.readlines()

def r(row, role):
    # row is 1-indexed to match grep output
    idx = row - 1
    lines[idx] = lines[idx].replace("self.llm", f'self.get_llm_for_role("{role}")')

r(949, "designer")
r(2495, "designer")
r(2729, "fixer")
r(2828, "fixer")
r(3881, "verifier")
r(4230, "fixer")
r(4434, "fixer")
r(4627, "physical")
r(4844, "manager")
r(5096, "physical")

with open(path, "w") as f:
    f.writelines(lines)
print("Line replacements completed.")
