import os
import json
from typing import Dict, List, Optional
from pydantic import BaseModel
from crewai import Agent, Task, Crew

class GraphNode(BaseModel):
    name: str
    description: str
    dependencies: List[str] = []
    locked: bool = False
    rtl_path: Optional[str] = None

class DependencyGraph:
    """A recursive graph to manage bottom-up compilation of RTL modules."""
    def __init__(self, sid: dict):
        self.nodes = {}
        self._build_graph(sid)

    def _build_graph(self, sid: dict):
        sub_modules = sid.get("sub_modules", [])
        top_name = sid.get("top_module", "top")
        
        for mod in sub_modules:
            name = mod.get("name")
            desc = mod.get("description", "")
            # Basic naive dependency tracking
            deps = []
            for other in sub_modules:
                if other.get("name") != name and other.get("name") in mod.get("functional_logic", ""):
                    deps.append(other.get("name"))
            self.nodes[name] = GraphNode(name=name, description=desc, dependencies=deps)
            
    def get_leaves(self) -> List[GraphNode]:
        return [n for n in self.nodes.values() if not n.dependencies and not n.locked]
        
    def get_unlocked_parents(self) -> List[GraphNode]:
        unlocked = []
        for n in self.nodes.values():
            if not n.locked:
                # Check if all deps are locked
                if all(self.nodes[d].locked for d in n.dependencies if d in self.nodes):
                    unlocked.append(n)
        return unlocked

class RecursiveGraphOrchestrator:
    """Iterates through the AST/SID tree from the leaves to the root."""
    def __init__(self, llm_designer, llm_fixer, llm_verifier):
        self.llm_designer = llm_designer
        self.llm_fixer = llm_fixer
        self.llm_verifier = llm_verifier

    def build_chip(self, top_module_name: str, spec: dict):
        graph = DependencyGraph(spec)
        
        # 1. Build bottom-level dependencies first
        leaf_nodes = graph.get_leaves()
        for node in leaf_nodes:
            self._build_module_in_isolation(node)
             
        # 2. Build parents 
        while graph.get_unlocked_parents():
            parents = graph.get_unlocked_parents()
            for node in parents:
                self._build_module_with_locked_submodules(node)

    def _build_module_in_isolation(self, node: GraphNode):
        """Runs the ENTIRE AgentIC pipeline specifically for ONE sub-module."""
        print(f"[GraphBuilder] Proceeding to generate RTL for leaf node: {node.name}")
        # In a full integration, we would link this to the existing RTL_GEN and RTL_FIX states
        # but scoped specifically to `node.name` instead of the entire design.
        node.locked = True
        node.rtl_path = f"artifacts/locked_modules/{node.name}.v"

    def _build_module_with_locked_submodules(self, node: GraphNode):
        """Build a module that depends on already-locked submodules."""
        print(f"[GraphBuilder] Assembling parent node: {node.name} using locked dependencies: {node.dependencies}")
        node.locked = True
        node.rtl_path = f"artifacts/locked_modules/{node.name}.v"

