"""
Contracts - Data classes for pipeline stage inputs/outputs.

Each stage has a strictly defined contract for what it receives and produces.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Literal
from datetime import datetime


# =============================================================================
# Intent Stage Contracts
# =============================================================================

@dataclass
class AudienceInfo:
    """Target audience information."""
    role: str
    knowledge_level: Literal["beginner", "intermediate", "expert"]


@dataclass
class ToneInfo:
    """Article tone settings."""
    formality: Literal["casual", "neutral", "formal"]
    style: Literal["practical", "analytical", "inspirational", "educational"]


@dataclass
class WordCountRange:
    """Target word count boundaries."""
    min: int  # >= 500
    max: int  # <= 10000


@dataclass
class IntentResult:
    """
    Output of Intent Analysis stage.

    Defines the editorial contract for article generation:
    - What the user is looking for (intent)
    - What the article should achieve
    - Tone, depth, and format requirements
    """
    topic: str
    region: str
    primary_intent: Literal["informational", "transactional", "commercial", "navigational"]
    user_goal: str
    article_goal: str
    content_type: Literal["guide", "how-to", "listicle", "comparison", "review", "explainer", "news", "opinion"]
    audience: AudienceInfo
    tone: ToneInfo
    depth: Literal["overview", "standard", "deep-dive"]
    word_count_range: WordCountRange
    must_answer_questions: List[str]  # 3-7 items
    must_not_include: List[str]
    success_criteria: List[str]

    @classmethod
    def from_dict(cls, data: dict) -> "IntentResult":
        """Create IntentResult from dict (e.g., parsed JSON)."""
        return cls(
            topic=data["topic"],
            region=data["region"],
            primary_intent=data["primary_intent"],
            user_goal=data["user_goal"],
            article_goal=data["article_goal"],
            content_type=data["content_type"],
            audience=AudienceInfo(
                role=data["audience"]["role"],
                knowledge_level=data["audience"]["knowledge_level"],
            ),
            tone=ToneInfo(
                formality=data["tone"]["formality"],
                style=data["tone"]["style"],
            ),
            depth=data["depth"],
            word_count_range=WordCountRange(
                min=data["word_count_range"]["min"],
                max=data["word_count_range"]["max"],
            ),
            must_answer_questions=data["must_answer_questions"],
            must_not_include=data.get("must_not_include", []),
            success_criteria=data.get("success_criteria", []),
        )

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "topic": self.topic,
            "region": self.region,
            "primary_intent": self.primary_intent,
            "user_goal": self.user_goal,
            "article_goal": self.article_goal,
            "content_type": self.content_type,
            "audience": {
                "role": self.audience.role,
                "knowledge_level": self.audience.knowledge_level,
            },
            "tone": {
                "formality": self.tone.formality,
                "style": self.tone.style,
            },
            "depth": self.depth,
            "word_count_range": {
                "min": self.word_count_range.min,
                "max": self.word_count_range.max,
            },
            "must_answer_questions": self.must_answer_questions,
            "must_not_include": self.must_not_include,
            "success_criteria": self.success_criteria,
        }


# =============================================================================
# Research Stage Contracts
# =============================================================================

@dataclass
class Query:
    """A search query with its purpose."""
    query: str
    purpose: Literal[
        "definition", "explanation", "how_it_works", "examples",
        "risks", "edge_cases", "comparison", "regulation",
        "pricing", "tooling", "other"
    ]


@dataclass
class QueryPlannerResult:
    """Output of Query Planner sub-stage."""
    topic: str
    queries: List[Query]

    @classmethod
    def from_dict(cls, data: dict) -> "QueryPlannerResult":
        return cls(
            topic=data["topic"],
            queries=[
                Query(query=q["query"], purpose=q["purpose"])
                for q in data["queries"]
            ],
        )

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "queries": [
                {"query": q.query, "purpose": q.purpose}
                for q in self.queries
            ],
        }


@dataclass
class Source:
    """A source used in research."""
    id: str
    title: str
    publisher: str
    url: str
    published_date: Optional[str]  # YYYY-MM-DD or None
    source_type: Literal["official", "documentation", "expert", "media", "community", "other"]
    relevance_notes: str


@dataclass
class Definition:
    """A definition of a key term."""
    id: str
    term: str
    definition: str
    source_id: str
    confidence: Literal["high", "medium", "low"]


@dataclass
class Fact:
    """An atomic fact with evidence."""
    id: str
    category: Literal[
        "definition", "rule", "process", "best_practice", "risk",
        "example", "legal", "pricing", "tooling", "other"
    ]
    claim: str
    evidence: str
    source_id: str
    confidence: Literal["high", "medium", "low"]


@dataclass
class Number:
    """A numeric value with context."""
    id: str
    metric: str
    value: str  # Can be string like "5-7" or number
    context: str
    source_id: str
    published_date: Optional[str]
    confidence: Literal["high", "medium", "low"]


@dataclass
class Example:
    """A practical example."""
    id: str
    example: str
    why_it_matters: str
    source_id: Optional[str]
    confidence: Literal["high", "medium", "low"]


@dataclass
class EdgeCase:
    """An edge case or exception."""
    id: str
    case: str
    impact: str
    source_id: Optional[str]
    confidence: Literal["high", "medium", "low"]


@dataclass
class Pitfall:
    """A common mistake or myth."""
    id: str
    item: str
    why_wrong_or_risky: str
    source_id: Optional[str]
    confidence: Literal["high", "medium", "low"]


@dataclass
class Contradiction:
    """Conflicting information between sources."""
    topic: str
    position_a: str
    source_a_id: str
    position_b: str
    source_b_id: str
    notes: str


@dataclass
class CoverageItem:
    """Coverage mapping for a must_answer_question."""
    must_answer_question: str
    supporting_fact_ids: List[str]
    supporting_number_ids: List[str]
    supporting_example_ids: List[str]
    coverage_confidence: Literal["high", "medium", "low"]
    missing_notes: Optional[str]


@dataclass
class ResearchResult:
    """
    Output of Research stage (Fact Packer).

    Contains all verified facts, sources, and coverage mapping.
    """
    topic: str
    region: str
    generated_at: str  # ISO-8601
    queries_used: List[str]
    sources: List[Source]
    definitions: List[Definition]
    facts: List[Fact]
    numbers: List[Number]
    examples: List[Example]
    edge_cases: List[EdgeCase]
    pitfalls_and_myths: List[Pitfall]
    contradictions: List[Contradiction]
    coverage_map: List[CoverageItem]

    @classmethod
    def from_dict(cls, data: dict) -> "ResearchResult":
        """Create ResearchResult from dict (e.g., parsed JSON)."""
        return cls(
            topic=data["topic"],
            region=data["region"],
            generated_at=data["generated_at"],
            queries_used=data.get("queries_used", []),
            sources=[
                Source(
                    id=s["id"],
                    title=s["title"],
                    publisher=s["publisher"],
                    url=s["url"],
                    published_date=s.get("published_date"),
                    source_type=s["source_type"],
                    relevance_notes=s.get("relevance_notes", ""),
                )
                for s in data.get("sources", [])
            ],
            definitions=[
                Definition(
                    id=d["id"],
                    term=d["term"],
                    definition=d["definition"],
                    source_id=d["source_id"],
                    confidence=d["confidence"],
                )
                for d in data.get("definitions", [])
            ],
            facts=[
                Fact(
                    id=f["id"],
                    category=f["category"],
                    claim=f["claim"],
                    evidence=f["evidence"],
                    source_id=f["source_id"],
                    confidence=f["confidence"],
                )
                for f in data.get("facts", [])
            ],
            numbers=[
                Number(
                    id=n["id"],
                    metric=n["metric"],
                    value=n["value"],
                    context=n["context"],
                    source_id=n["source_id"],
                    published_date=n.get("published_date"),
                    confidence=n["confidence"],
                )
                for n in data.get("numbers", [])
            ],
            examples=[
                Example(
                    id=e["id"],
                    example=e["example"],
                    why_it_matters=e["why_it_matters"],
                    source_id=e.get("source_id"),
                    confidence=e["confidence"],
                )
                for e in data.get("examples", [])
            ],
            edge_cases=[
                EdgeCase(
                    id=ec["id"],
                    case=ec["case"],
                    impact=ec["impact"],
                    source_id=ec.get("source_id"),
                    confidence=ec["confidence"],
                )
                for ec in data.get("edge_cases", [])
            ],
            pitfalls_and_myths=[
                Pitfall(
                    id=p["id"],
                    item=p["item"],
                    why_wrong_or_risky=p["why_wrong_or_risky"],
                    source_id=p.get("source_id"),
                    confidence=p["confidence"],
                )
                for p in data.get("pitfalls_and_myths", [])
            ],
            contradictions=[
                Contradiction(
                    topic=c["topic"],
                    position_a=c["position_a"],
                    source_a_id=c["source_a_id"],
                    position_b=c["position_b"],
                    source_b_id=c["source_b_id"],
                    notes=c.get("notes", ""),
                )
                for c in data.get("contradictions", [])
            ],
            coverage_map=[
                CoverageItem(
                    must_answer_question=cm["must_answer_question"],
                    supporting_fact_ids=cm.get("supporting_fact_ids", []),
                    supporting_number_ids=cm.get("supporting_number_ids", []),
                    supporting_example_ids=cm.get("supporting_example_ids", []),
                    coverage_confidence=cm["coverage_confidence"],
                    missing_notes=cm.get("missing_notes"),
                )
                for cm in data.get("coverage_map", [])
            ],
        )

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "topic": self.topic,
            "region": self.region,
            "generated_at": self.generated_at,
            "queries_used": self.queries_used,
            "sources": [
                {
                    "id": s.id,
                    "title": s.title,
                    "publisher": s.publisher,
                    "url": s.url,
                    "published_date": s.published_date,
                    "source_type": s.source_type,
                    "relevance_notes": s.relevance_notes,
                }
                for s in self.sources
            ],
            "definitions": [
                {
                    "id": d.id,
                    "term": d.term,
                    "definition": d.definition,
                    "source_id": d.source_id,
                    "confidence": d.confidence,
                }
                for d in self.definitions
            ],
            "facts": [
                {
                    "id": f.id,
                    "category": f.category,
                    "claim": f.claim,
                    "evidence": f.evidence,
                    "source_id": f.source_id,
                    "confidence": f.confidence,
                }
                for f in self.facts
            ],
            "numbers": [
                {
                    "id": n.id,
                    "metric": n.metric,
                    "value": n.value,
                    "context": n.context,
                    "source_id": n.source_id,
                    "published_date": n.published_date,
                    "confidence": n.confidence,
                }
                for n in self.numbers
            ],
            "examples": [
                {
                    "id": e.id,
                    "example": e.example,
                    "why_it_matters": e.why_it_matters,
                    "source_id": e.source_id,
                    "confidence": e.confidence,
                }
                for e in self.examples
            ],
            "edge_cases": [
                {
                    "id": ec.id,
                    "case": ec.case,
                    "impact": ec.impact,
                    "source_id": ec.source_id,
                    "confidence": ec.confidence,
                }
                for ec in self.edge_cases
            ],
            "pitfalls_and_myths": [
                {
                    "id": p.id,
                    "item": p.item,
                    "why_wrong_or_risky": p.why_wrong_or_risky,
                    "source_id": p.source_id,
                    "confidence": p.confidence,
                }
                for p in self.pitfalls_and_myths
            ],
            "contradictions": [
                {
                    "topic": c.topic,
                    "position_a": c.position_a,
                    "source_a_id": c.source_a_id,
                    "position_b": c.position_b,
                    "source_b_id": c.source_b_id,
                    "notes": c.notes,
                }
                for c in self.contradictions
            ],
            "coverage_map": [
                {
                    "must_answer_question": cm.must_answer_question,
                    "supporting_fact_ids": cm.supporting_fact_ids,
                    "supporting_number_ids": cm.supporting_number_ids,
                    "supporting_example_ids": cm.supporting_example_ids,
                    "coverage_confidence": cm.coverage_confidence,
                    "missing_notes": cm.missing_notes,
                }
                for cm in self.coverage_map
            ],
        }


# =============================================================================
# Structure Stage Contracts
# =============================================================================

@dataclass
class SourceRefs:
    """References to research pack items."""
    fact_ids: List[str] = field(default_factory=list)
    definition_ids: List[str] = field(default_factory=list)
    number_ids: List[str] = field(default_factory=list)
    example_ids: List[str] = field(default_factory=list)
    pitfall_ids: List[str] = field(default_factory=list)


@dataclass
class ContentBlock:
    """A content block within a section."""
    type: Literal["explanation", "list", "steps", "table", "example", "faq", "warning"]
    goal: str
    source_refs: SourceRefs


@dataclass
class Subsection:
    """H3 subsection within a section."""
    id: str
    h3: str
    purpose: str
    word_count_target: int
    must_answer_questions: List[str]
    content_blocks: List[ContentBlock]


@dataclass
class Section:
    """H2 section in the outline."""
    id: str
    h2: str
    purpose: str
    word_count_target: int
    must_answer_questions: List[str]
    content_blocks: List[ContentBlock]
    subsections: List[Subsection] = field(default_factory=list)


@dataclass
class Introduction:
    """Introduction section."""
    purpose: str
    key_points: List[str]
    word_count_target: int


@dataclass
class Conclusion:
    """Conclusion section."""
    purpose: str
    takeaways: List[str]
    word_count_target: int


@dataclass
class CoverageCheck:
    """Coverage validation result."""
    all_must_answer_covered: bool
    uncovered_questions: List[str]
    missing_notes: Optional[str]


@dataclass
class OutlineResult:
    """
    Output of Structure stage.

    Defines the article architecture with word count distribution.
    """
    title: str
    subtitle: str
    target_total_words: int
    introduction: Introduction
    sections: List[Section]
    conclusion: Conclusion
    coverage_check: CoverageCheck

    @classmethod
    def from_dict(cls, data: dict) -> "OutlineResult":
        """Create OutlineResult from dict (e.g., parsed JSON)."""
        def parse_source_refs(sr: dict) -> SourceRefs:
            return SourceRefs(
                fact_ids=sr.get("fact_ids", []),
                definition_ids=sr.get("definition_ids", []),
                number_ids=sr.get("number_ids", []),
                example_ids=sr.get("example_ids", []),
                pitfall_ids=sr.get("pitfall_ids", []),
            )

        def parse_content_block(cb: dict) -> ContentBlock:
            return ContentBlock(
                type=cb["type"],
                goal=cb["goal"],
                source_refs=parse_source_refs(cb.get("source_refs", {})),
            )

        def parse_subsection(ss: dict) -> Subsection:
            return Subsection(
                id=ss["id"],
                h3=ss["h3"],
                purpose=ss["purpose"],
                word_count_target=ss["word_count_target"],
                must_answer_questions=ss.get("must_answer_questions", []),
                content_blocks=[parse_content_block(cb) for cb in ss.get("content_blocks", [])],
            )

        def parse_section(s: dict) -> Section:
            return Section(
                id=s["id"],
                h2=s["h2"],
                purpose=s["purpose"],
                word_count_target=s["word_count_target"],
                must_answer_questions=s.get("must_answer_questions", []),
                content_blocks=[parse_content_block(cb) for cb in s.get("content_blocks", [])],
                subsections=[parse_subsection(ss) for ss in s.get("subsections", [])],
            )

        return cls(
            title=data["title"],
            subtitle=data["subtitle"],
            target_total_words=data["target_total_words"],
            introduction=Introduction(
                purpose=data["introduction"]["purpose"],
                key_points=data["introduction"]["key_points"],
                word_count_target=data["introduction"]["word_count_target"],
            ),
            sections=[parse_section(s) for s in data["sections"]],
            conclusion=Conclusion(
                purpose=data["conclusion"]["purpose"],
                takeaways=data["conclusion"]["takeaways"],
                word_count_target=data["conclusion"]["word_count_target"],
            ),
            coverage_check=CoverageCheck(
                all_must_answer_covered=data["coverage_check"]["all_must_answer_covered"],
                uncovered_questions=data["coverage_check"].get("uncovered_questions", []),
                missing_notes=data["coverage_check"].get("missing_notes"),
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        def source_refs_to_dict(sr: SourceRefs) -> dict:
            return {
                "fact_ids": sr.fact_ids,
                "definition_ids": sr.definition_ids,
                "number_ids": sr.number_ids,
                "example_ids": sr.example_ids,
                "pitfall_ids": sr.pitfall_ids,
            }

        def content_block_to_dict(cb: ContentBlock) -> dict:
            return {
                "type": cb.type,
                "goal": cb.goal,
                "source_refs": source_refs_to_dict(cb.source_refs),
            }

        def subsection_to_dict(ss: Subsection) -> dict:
            return {
                "id": ss.id,
                "h3": ss.h3,
                "purpose": ss.purpose,
                "word_count_target": ss.word_count_target,
                "must_answer_questions": ss.must_answer_questions,
                "content_blocks": [content_block_to_dict(cb) for cb in ss.content_blocks],
            }

        def section_to_dict(s: Section) -> dict:
            return {
                "id": s.id,
                "h2": s.h2,
                "purpose": s.purpose,
                "word_count_target": s.word_count_target,
                "must_answer_questions": s.must_answer_questions,
                "content_blocks": [content_block_to_dict(cb) for cb in s.content_blocks],
                "subsections": [subsection_to_dict(ss) for ss in s.subsections],
            }

        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "target_total_words": self.target_total_words,
            "introduction": {
                "purpose": self.introduction.purpose,
                "key_points": self.introduction.key_points,
                "word_count_target": self.introduction.word_count_target,
            },
            "sections": [section_to_dict(s) for s in self.sections],
            "conclusion": {
                "purpose": self.conclusion.purpose,
                "takeaways": self.conclusion.takeaways,
                "word_count_target": self.conclusion.word_count_target,
            },
            "coverage_check": {
                "all_must_answer_covered": self.coverage_check.all_must_answer_covered,
                "uncovered_questions": self.coverage_check.uncovered_questions,
                "missing_notes": self.coverage_check.missing_notes,
            },
        }


# =============================================================================
# Pipeline Result
# =============================================================================

@dataclass
class PipelineResult:
    """
    Final output of the writing pipeline.

    Contains the edited article and all intermediate results.
    """
    topic: str
    region: str

    # Final output
    article_md: str
    title: str
    subtitle: str
    word_count: int

    # Intermediate results (for debugging/logging)
    intent: IntentResult
    research: ResearchResult
    outline: OutlineResult
    draft_md: str

    # Metadata
    started_at: str
    completed_at: str
    stages_completed: List[str]

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "topic": self.topic,
            "region": self.region,
            "article_md": self.article_md,
            "title": self.title,
            "subtitle": self.subtitle,
            "word_count": self.word_count,
            "intent": self.intent.to_dict(),
            "research": self.research.to_dict(),
            "outline": self.outline.to_dict(),
            "draft_md": self.draft_md,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stages_completed": self.stages_completed,
        }
