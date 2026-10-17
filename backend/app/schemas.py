from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(..., min_length=1, max_length=500)


class ChatResponse(BaseModel):
    investigation_id: str
    status: str
    intent: Optional[str] = None
    question: str
    as_of_date: str
    explanation_mode: str
    risk_level: Optional[str] = None
    answer: str
    root_cause: Optional[Dict[str, Any]] = None
    verified_facts: List[Dict[str, Any]] = []
    calculated_estimates: List[Dict[str, Any]] = []
    assumptions: List[Dict[str, Any]] = []
    affected_entities: List[Dict[str, Any]] = []
    not_exposed_entities: List[Dict[str, Any]] = []
    graph: Dict[str, Any] = {"nodes": [], "edges": []}
    evidence_paths: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []
    recommendations: List[Dict[str, Any]] = []
    data_quality_issues: List[Dict[str, Any]] = []
    limitations: List[str] = []
    queries_executed: List[Dict[str, Any]] = []
    clarification: Optional[Dict[str, Any]] = None
    supported_examples: List[str] = []


class InvestigationSummary(BaseModel):
    investigation_id: str
    question: str
    status: str
    risk_level: Optional[str] = None
    created_at: str