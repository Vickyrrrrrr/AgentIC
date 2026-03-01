"""
ReAct Agent Framework — Reasoning and Acting
=============================================

Implements the ReAct prompting technique (Yao et al., 2023) for all 
agent interactions in the AgentIC multi-agent pipeline.

ReAct Pattern:
    Thought → Action → Observation → Thought → Action → ...

Each agent step follows this loop:
  1. THOUGHT: Reason about the current state and what needs to happen
  2. ACTION:  Choose and execute one of the available tools
  3. OBSERVATION: Observe the result of the action
  4. Repeat until the task is complete or max steps reached

This replaces ad-hoc LLM prompting with structured, traceable reasoning.
"""

import json
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ─── Data Structures ─────────────────────────────────────────────────

class StepStatus(Enum):
    THOUGHT = "THOUGHT"
    ACTION = "ACTION"
    OBSERVATION = "OBSERVATION"
    FINAL_ANSWER = "FINAL_ANSWER"
    ERROR = "ERROR"


@dataclass
class ReActStep:
    """A single step in the ReAct reasoning chain."""
    step_num: int
    status: StepStatus
    content: str
    action_name: str = ""
    action_input: str = ""
    observation: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "step": self.step_num,
            "status": self.status.value,
            "content": self.content,
            "action_name": self.action_name,
            "action_input": self.action_input[:500],
            "observation": self.observation[:500],
        }


@dataclass
class ReActTrace:
    """Complete trace of a ReAct agent run."""
    task_description: str
    steps: List[ReActStep] = field(default_factory=list)
    final_answer: str = ""
    success: bool = False
    total_steps: int = 0
    total_duration_s: float = 0.0
    error: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "task": self.task_description[:200],
            "success": self.success,
            "total_steps": self.total_steps,
            "total_duration_s": round(self.total_duration_s, 2),
            "steps": [s.to_dict() for s in self.steps],
            "final_answer": self.final_answer[:2000],
            "error": self.error,
        }, indent=2)


# ─── Tool Registry ───────────────────────────────────────────────────

@dataclass
class ToolDef:
    """Definition of a tool available to the ReAct agent."""
    name: str
    description: str
    function: Callable
    parameters: Dict[str, str] = field(default_factory=dict)  # param_name → description


class ToolRegistry:
    """Registry of tools available to ReAct agents."""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(self, name: str, description: str, func: Callable,
                 parameters: Optional[Dict[str, str]] = None):
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            function=func,
            parameters=parameters or {},
        )

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_tools(self) -> str:
        """Format tools for the ReAct prompt."""
        lines = []
        for name, tool in self._tools.items():
            params = ", ".join(f"{k}: {v}" for k, v in tool.parameters.items())
            lines.append(f"  {name}({params}) — {tool.description}")
        return "\n".join(lines)

    def execute(self, name: str, input_str: str) -> str:
        """Execute a tool by name with the given input string."""
        tool = self._tools.get(name)
        if not tool:
            return f"ERROR: Unknown tool '{name}'. Available: {', '.join(self._tools.keys())}"
        try:
            result = tool.function(input_str)
            return str(result) if result is not None else "OK"
        except Exception as e:
            return f"ERROR: {name} failed: {str(e)}"


# ─── ReAct Prompt Templates ──────────────────────────────────────────

REACT_SYSTEM_PROMPT = """\
You are an expert VLSI agent using the ReAct (Reasoning and Acting) framework.

On each turn you must output ONE of:
  Thought: <your reasoning about the current state>
  Action: <tool_name>(<input>)
  Final Answer: <your complete answer>

RULES:
1. Always start with a Thought before taking any Action.
2. After each Action, wait for the Observation before your next Thought.
3. You MUST use the available tools — do not hallucinate tool outputs.
4. When you have enough information, produce a Final Answer.
5. Maximum {max_steps} steps — be efficient.
6. If an action fails, reason about WHY and try a different approach.

Available Tools:
{tools}

TASK: {task}
"""

REACT_OBSERVATION_PROMPT = """\
Observation: {observation}

Continue with your next Thought or provide your Final Answer.
"""


# ─── ReAct Agent ──────────────────────────────────────────────────────

class ReActAgent:
    """
    General-purpose ReAct agent for the AgentIC pipeline.
    
    Uses the ReAct (Reasoning + Acting) prompting technique to provide
    structured, traceable reasoning for all agent interactions.
    
    Usage:
        agent = ReActAgent(llm, role="RTL Debugger")
        agent.register_tool("syntax_check", "Check Verilog syntax", syntax_check_fn)
        agent.register_tool("read_file", "Read a file", read_file_fn)
        
        trace = agent.run("Fix the syntax error in counter.v")
        print(trace.final_answer)
    """

    def __init__(
        self,
        llm,               # CrewAI LLM instance
        role: str = "VLSI Agent",
        max_steps: int = 10,
        verbose: bool = False,
    ):
        self.llm = llm
        self.role = role
        self.max_steps = max_steps
        self.verbose = verbose
        self.tools = ToolRegistry()
        self._conversation: List[Dict[str, str]] = []

    def register_tool(self, name: str, description: str, func: Callable,
                      parameters: Optional[Dict[str, str]] = None):
        """Register a tool available to this agent."""
        self.tools.register(name, description, func, parameters)

    def run(self, task: str, context: str = "") -> ReActTrace:
        """
        Execute the ReAct loop for the given task.
        
        Args:
            task:    Natural language task description
            context: Additional context (RTL code, error logs, etc.)
            
        Returns:
            ReActTrace with complete reasoning chain and final answer.
        """
        trace = ReActTrace(task_description=task)
        start_time = time.time()

        # Build system prompt
        system_prompt = REACT_SYSTEM_PROMPT.format(
            max_steps=self.max_steps,
            tools=self.tools.list_tools(),
            task=task,
        )

        if context:
            system_prompt += f"\n\nCONTEXT:\n{context[:8000]}"

        self._conversation = [{"role": "system", "content": system_prompt}]

        step_num = 0
        while step_num < self.max_steps:
            step_num += 1
            step_start = time.time()

            # Get LLM response
            try:
                response = self._call_llm()
            except Exception as e:
                trace.steps.append(ReActStep(
                    step_num=step_num,
                    status=StepStatus.ERROR,
                    content=f"LLM call failed: {str(e)}",
                ))
                trace.error = str(e)
                break

            # Parse the response
            thought, action_name, action_input, final_answer = self._parse_response(response)

            # Handle FINAL ANSWER
            if final_answer:
                trace.steps.append(ReActStep(
                    step_num=step_num,
                    status=StepStatus.FINAL_ANSWER,
                    content=final_answer,
                    duration_s=time.time() - step_start,
                ))
                trace.final_answer = final_answer
                trace.success = True
                break

            # Handle THOUGHT
            if thought:
                trace.steps.append(ReActStep(
                    step_num=step_num,
                    status=StepStatus.THOUGHT,
                    content=thought,
                    duration_s=time.time() - step_start,
                ))
                if self.verbose:
                    logger.info(f"[ReAct:{self.role}] Thought: {thought[:200]}")

            # Handle ACTION
            if action_name:
                # Execute the tool
                observation = self.tools.execute(action_name, action_input)

                trace.steps.append(ReActStep(
                    step_num=step_num,
                    status=StepStatus.ACTION,
                    content=f"{action_name}({action_input[:200]})",
                    action_name=action_name,
                    action_input=action_input,
                    observation=observation[:2000],
                    duration_s=time.time() - step_start,
                ))

                if self.verbose:
                    logger.info(f"[ReAct:{self.role}] Action: {action_name} → {observation[:200]}")

                # Feed observation back
                obs_prompt = REACT_OBSERVATION_PROMPT.format(
                    observation=observation[:4000]
                )
                self._conversation.append({"role": "assistant", "content": response})
                self._conversation.append({"role": "user", "content": obs_prompt})

            elif not thought and not final_answer:
                # LLM produced something unparseable — nudge it
                self._conversation.append({"role": "assistant", "content": response})
                self._conversation.append({
                    "role": "user",
                    "content": (
                        "Your response didn't follow the ReAct format. "
                        "Please respond with one of:\n"
                        "  Thought: <reasoning>\n"
                        "  Action: <tool_name>(<input>)\n"
                        "  Final Answer: <answer>"
                    ),
                })

        trace.total_steps = step_num
        trace.total_duration_s = time.time() - start_time

        if not trace.success:
            trace.error = trace.error or "Max steps reached without Final Answer"
            # Use last thought/action as fallback answer
            for step in reversed(trace.steps):
                if step.content:
                    trace.final_answer = step.content
                    break

        return trace

    def _call_llm(self) -> str:
        """Call the LLM with the current conversation."""
        from crewai import Agent, Task, Crew

        # Build a single prompt from conversation history
        prompt_parts = []
        for msg in self._conversation:
            if msg["role"] == "system":
                prompt_parts.append(msg["content"])
            elif msg["role"] == "user":
                prompt_parts.append(f"\n{msg['content']}")
            elif msg["role"] == "assistant":
                prompt_parts.append(f"\nAssistant: {msg['content']}")

        full_prompt = "\n".join(prompt_parts)

        agent = Agent(
            role=self.role,
            goal="Follow the ReAct framework to complete the task",
            backstory=f"Expert {self.role} using structured ReAct reasoning.",
            llm=self.llm,
            verbose=False,  # We handle our own logging
        )

        task = Task(
            description=full_prompt[-12000:],  # Truncate to fit context
            expected_output="A Thought, Action, or Final Answer following ReAct format",
            agent=agent,
        )

        result = str(Crew(agents=[agent], tasks=[task]).kickoff())
        return result

    def _parse_response(self, response: str) -> Tuple[str, str, str, str]:
        """
        Parse a ReAct response into (thought, action_name, action_input, final_answer).
        
        Returns empty strings for components not present in the response.
        """
        thought = ""
        action_name = ""
        action_input = ""
        final_answer = ""

        # Check for Final Answer
        fa_match = re.search(r'Final\s+Answer\s*:\s*(.+)', response, re.DOTALL | re.IGNORECASE)
        if fa_match:
            final_answer = fa_match.group(1).strip()
            return thought, action_name, action_input, final_answer

        # Check for Thought
        th_match = re.search(r'Thought\s*:\s*(.+?)(?=Action\s*:|Final\s+Answer\s*:|$)',
                             response, re.DOTALL | re.IGNORECASE)
        if th_match:
            thought = th_match.group(1).strip()

        # Check for Action
        act_match = re.search(r'Action\s*:\s*(\w+)\s*\((.+?)\)\s*$',
                              response, re.MULTILINE | re.IGNORECASE)
        if not act_match:
            # Try alternative format: Action: tool_name\nAction Input: input
            act_match2 = re.search(r'Action\s*:\s*(\w+)', response, re.IGNORECASE)
            inp_match = re.search(r'Action\s+Input\s*:\s*(.+?)(?=\n|$)', response,
                                  re.DOTALL | re.IGNORECASE)
            if act_match2:
                action_name = act_match2.group(1).strip()
                action_input = inp_match.group(1).strip() if inp_match else ""
        else:
            action_name = act_match.group(1).strip()
            action_input = act_match.group(2).strip()

        return thought, action_name, action_input, final_answer


# ─── Pre-built ReAct Agents for AgentIC Pipeline ─────────────────────

def create_rtl_debugger_agent(llm, tools_dict: Dict[str, Callable],
                               verbose: bool = False) -> ReActAgent:
    """Create a ReAct agent pre-configured for RTL debugging."""
    agent = ReActAgent(llm, role="RTL Debugger", max_steps=8, verbose=verbose)
    
    default_tools = {
        "syntax_check": ("Check Verilog syntax of a file path", {}),
        "read_file": ("Read contents of a file path", {}),
        "run_simulation": ("Run Icarus Verilog simulation for a design name", {}),
        "trace_signal": ("Back-trace a signal through the RTL AST", {}),
    }

    for name, func in tools_dict.items():
        desc, params = default_tools.get(name, (f"Execute {name}", {}))
        agent.register_tool(name, desc, func, params)

    return agent


def create_formal_debugger_agent(llm, tools_dict: Dict[str, Callable],
                                  verbose: bool = False) -> ReActAgent:
    """Create a ReAct agent pre-configured for formal verification debugging."""
    agent = ReActAgent(llm, role="Formal Verification Debugger", max_steps=10, verbose=verbose)

    default_tools = {
        "run_formal": ("Run SymbiYosys formal verification on a .sby file", {}),
        "read_file": ("Read contents of a file path", {}),
        "analyze_signal": ("Run balanced for-and-against analysis on a signal", {}),
        "build_causal_graph": ("Build causal graph from RTL and failure", {}),
    }

    for name, func in tools_dict.items():
        desc, params = default_tools.get(name, (f"Execute {name}", {}))
        agent.register_tool(name, desc, func, params)

    return agent


def create_architect_agent(llm, tools_dict: Dict[str, Callable],
                            verbose: bool = False) -> ReActAgent:
    """Create a ReAct agent pre-configured for architectural decomposition."""
    agent = ReActAgent(llm, role="Spec2RTL Architect", max_steps=6, verbose=verbose)

    default_tools = {
        "decompose_spec": ("Decompose a natural language spec into JSON SID", {}),
        "validate_sid": ("Validate a Structured Information Dictionary", {}),
        "read_spec": ("Read a specification file (text or PDF)", {}),
    }

    for name, func in tools_dict.items():
        desc, params = default_tools.get(name, (f"Execute {name}", {}))
        agent.register_tool(name, desc, func, params)

    return agent
