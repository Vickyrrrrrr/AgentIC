import re

path = "src/agentic/orchestrator.py"
content = open(path).read()

# 1. architect
content = content.replace('ArchitectModule(llm=self.llm', 'ArchitectModule(llm=self.get_llm_for_role("architect")')
content = content.replace('HardwareSpecGenerator(llm=self.llm', 'HardwareSpecGenerator(llm=self.get_llm_for_role("architect")')
content = content.replace('HierarchyExpander(llm=self.llm', 'HierarchyExpander(llm=self.get_llm_for_role("architect")')

# 2. designer
content = content.replace('DesignerModule(\n                llm=self.llm', 'DesignerModule(\n                llm=self.get_llm_for_role("designer")')
content = content.replace('get_designer_agent(\n                self.llm,', 'get_designer_agent(\n                self.get_llm_for_role("designer"),')

# 3. verifier
content = content.replace('get_testbench_agent(self.llm, f"Verify', 'get_testbench_agent(self.get_llm_for_role("verifier"), f"Verify')
content = content.replace('get_verification_agent(self.llm', 'get_verification_agent(self.get_llm_for_role("verifier")')
content = content.replace('get_testbench_agent(self.llm, f"Improve', 'get_testbench_agent(self.get_llm_for_role("verifier"), f"Improve')

# 4. debugger
content = content.replace('get_error_analyst_agent(self.llm', 'get_error_analyst_agent(self.get_llm_for_role("debugger")')

# 5. fixer
content = content.replace('get_testbench_agent(self.llm, f"Fix', 'get_testbench_agent(self.get_llm_for_role("fixer"), f"Fix')
content = content.replace('get_designer_agent(self.llm, f"Fix', 'get_designer_agent(self.get_llm_for_role("fixer"), f"Fix')
content = content.replace('get_designer_agent(\n            self.llm, \n            f"Fix RTL', 'get_designer_agent(\n            self.get_llm_for_role("fixer"), \n            f"Fix RTL')

# 6. physical
content = content.replace('get_sdc_agent(self.llm', 'get_sdc_agent(self.get_llm_for_role("physical")')

# 7. manager
content = content.replace('get_doc_agent(self.llm', 'get_doc_agent(self.get_llm_for_role("manager")')

# Handle generic ReActAgent instances based on role string proximity using regex
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="RTL Implementation Engineer"', r'ReActAgent(\n                llm=self.get_llm_for_role("designer"),\n                role="RTL Implementation Engineer"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="Syntax Fixer"', r'ReActAgent(\n                llm=self.get_llm_for_role("fixer"),\n                role="Syntax Fixer"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="RTL Verilog Expert"', r'ReActAgent(\n                llm=self.get_llm_for_role("fixer"),\n                role="RTL Verilog Expert"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="Testbench Engineer"', r'ReActAgent(\n                        llm=self.get_llm_for_role("verifier"),\n                        role="Testbench Engineer"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="ECO Engineer"', r'ReActAgent(\n                llm=self.get_llm_for_role("fixer"),\n                role="ECO Engineer"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="Floorplan Engineer"', r'ReActAgent(\n                llm=self.get_llm_for_role("physical"),\n                role="Floorplan Engineer"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="Convergence Expert"', r'ReActAgent(\n                    llm=self.get_llm_for_role("manager"),\n                    role="Convergence Expert"', content)
content = re.sub(r'ReActAgent\(\s*llm=self\.llm,\s*role="Physical Design Engineer"', r'ReActAgent(\n                        llm=self.get_llm_for_role("physical"),\n                        role="Physical Design Engineer"', content)

open(path, "w").write(content)
print("Done")
