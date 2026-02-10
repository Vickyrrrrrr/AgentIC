"""AgentIC Golden Reference Library.

Pre-verified RTL templates for common IP blocks.
Instead of generating from scratch, the LLM customizes proven templates.
"""

from .template_matcher import TemplateMatcher, get_best_template

__all__ = ['TemplateMatcher', 'get_best_template']
