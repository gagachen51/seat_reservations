"""领域层：模型、参数、规则与用例引擎。"""

from .engine import DomainEngine
from .models import Actor, Result, Role
from .params import ParamSet

__all__ = ["DomainEngine", "Actor", "Result", "Role", "ParamSet"]
