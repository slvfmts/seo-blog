"""
Contracts - Data classes for pipeline stage inputs/outputs.

Each stage has a strictly defined contract for what it receives and produces.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Literal, Dict, Any
from datetime import datetime


# =============================================================================
# Keyword Metrics Contract
# =============================================================================

@dataclass
class KeywordMetricsData:
    """Keyword metrics from VolumeProvider (Wordstat, Rush, etc.)."""
    keyword: str
    search_volume: int
    difficulty: float  # 0-100
    cpc: float
    competition: float  # 0-1
    competition_level: str  # LOW, MEDIUM, HIGH


@dataclass
class KeywordMetricsResult:
    """Result of keyword metrics fetch for research stage."""
    metrics: Dict[str, KeywordMetricsData]  # keyword -> metrics
    source: str  # "wordstat", "rush", "wordstat+rush", "none", etc.

    def get_volume(self, keyword: str) -> int:
        """Get search volume for keyword."""
        if keyword.lower() in self.metrics:
            return self.metrics[keyword.lower()].search_volume
        return 0

    def get_difficulty(self, keyword: str) -> float:
        """Get difficulty for keyword."""
        if keyword.lower() in self.metrics:
            return self.metrics[keyword.lower()].difficulty
        return 0

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "metrics": {
                k: {
                    "keyword": v.keyword,
                    "search_volume": v.search_volume,
                    "difficulty": v.difficulty,
                    "cpc": v.cpc,
                    "competition": v.competition,
                    "competition_level": v.competition_level,
                }
                for k, v in self.metrics.items()
            },
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KeywordMetricsResult":
        """Create from dict."""
        metrics = {}
        for k, v in data.get("metrics", {}).items():
            metrics[k] = KeywordMetricsData(
                keyword=v["keyword"],
                search_volume=v["search_volume"],
                difficulty=v["difficulty"],
                cpc=v["cpc"],
                competition=v["competition"],
                competition_level=v["competition_level"],
            )
        return cls(metrics=metrics, source=data.get("source", "unknown"))


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
class TopicBoundaries:
    """
    Topic scope guard - defines what is in/out of scope for the article.

    Prevents topic drift by explicitly listing:
    - in_scope: aspects that MUST be covered (3-5 items)
    - out_of_scope: related topics that should NOT be covered (3-5 items)
    """
    in_scope: List[str]
    out_of_scope: List[str]


@dataclass
class IntentResult:
    """
    Output of Intent Analysis stage.

    Defines the editorial contract for article generation:
    - What the user is looking for (intent)
    - What the article should achieve
    - Tone, depth, and format requirements
    - Topic boundaries to prevent scope drift
    """
    topic: str
    region: str
    primary_intent: Literal["informational", "transactional", "commercial", "navigational"]
    user_goal: str
    article_goal: str
    topic_boundaries: TopicBoundaries
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
        # Handle topic_boundaries - use defaults if not present (backwards compatibility)
        topic_boundaries_data = data.get("topic_boundaries", {})
        topic_boundaries = TopicBoundaries(
            in_scope=topic_boundaries_data.get("in_scope", []),
            out_of_scope=topic_boundaries_data.get("out_of_scope", []),
        )

        return cls(
            topic=data["topic"],
            region=data["region"],
            primary_intent=data["primary_intent"],
            user_goal=data["user_goal"],
            article_goal=data["article_goal"],
            topic_boundaries=topic_boundaries,
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
            "topic_boundaries": {
                "in_scope": self.topic_boundaries.in_scope,
                "out_of_scope": self.topic_boundaries.out_of_scope,
            },
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
class ClaimEvidence:
    """Evidence backing a claim."""
    source_title: str
    source_url: str
    supporting_quote_or_note: str  # ≤25 words quote or summary note


@dataclass
class ClaimItem:
    """A verified claim that may be used in the article."""
    claim_text: str  # 1-2 sentences
    claim_type: Literal["definition", "benchmark", "best_practice", "process", "metric", "tooling"]
    evidence: ClaimEvidence
    allowed_numeric: bool  # whether exact numbers may be used
    allowed_ranges: Optional[str] = None  # e.g. "5-15%"
    use_rules: str = ""  # context, segment, caveats


@dataclass
class ClaimBank:
    """Bank of verified claims and disallowed patterns."""
    allowed_claims: List[ClaimItem]
    disallowed_claim_patterns: List[str]  # e.g. "гарантированно", "всегда", regex-like


@dataclass
class UniqueAngle:
    """Unique editorial angle for the article."""
    article_role: Literal["pillar", "cluster"]
    primary_intent: str  # one phrase: why the reader came
    differentiators: List[str]  # 5-7 bullets: what makes this article unique
    must_not_cover: List[str]  # 5-7 bullets: topics covered elsewhere in cluster


@dataclass
class ClusterOverlapEntry:
    """Overlap with an existing post in the cluster."""
    post_slug: str
    overlap_topics: List[str]  # 3-7 topics already covered there
    avoid_sections: List[str]  # H2-level blocks not to repeat
    suggest_links: List[str]  # 1-3 places to link to this post


@dataclass
class ExampleSnippet:
    """A micro-example suitable for insertion in the article."""
    scenario: str  # e.g. "B2B SaaS с длинным внедрением"
    snippet: str  # 1-2 sentences
    where_to_use: Literal["intro", "process", "metrics", "strategy", "tools", "faq"]
    source_basis: str  # URL or "PAA/общепринятый паттерн"


@dataclass
class TerminologyCanon:
    """Canonical terminology for the article."""
    terms: Dict[str, str]  # term -> normalized spelling / explanation / when to use
    do_not_use: List[str]  # forbidden formulations, keyword-bag patterns


@dataclass
class FormattingAsset:
    """An asset produced by the formatting stage."""
    type: Literal["cover", "diagram"]
    filename: str
    path: str
    alt: str
    caption: str = ""
    ghost_url: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "filename": self.filename,
            "path": self.path,
            "alt": self.alt,
            "caption": self.caption,
            "ghost_url": self.ghost_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FormattingAsset":
        return cls(
            type=data["type"],
            filename=data["filename"],
            path=data["path"],
            alt=data["alt"],
            caption=data.get("caption", ""),
            ghost_url=data.get("ghost_url", ""),
        )


@dataclass
class FormattingResult:
    """Output of Formatting stage."""
    assets: List[FormattingAsset] = field(default_factory=list)
    cover_generated: bool = False
    diagrams_count: int = 0
    errors: List[str] = field(default_factory=list)
    cover_ghost_url: str = ""
    cover_image_alt: str = ""

    def to_dict(self) -> dict:
        return {
            "assets": [a.to_dict() for a in self.assets],
            "cover_generated": self.cover_generated,
            "diagrams_count": self.diagrams_count,
            "errors": self.errors,
            "cover_ghost_url": self.cover_ghost_url,
            "cover_image_alt": self.cover_image_alt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FormattingResult":
        return cls(
            assets=[FormattingAsset.from_dict(a) for a in data.get("assets", [])],
            cover_generated=data.get("cover_generated", False),
            diagrams_count=data.get("diagrams_count", 0),
            errors=data.get("errors", []),
            cover_ghost_url=data.get("cover_ghost_url", ""),
            cover_image_alt=data.get("cover_image_alt", ""),
        )


@dataclass
class QualityGateResult:
    """Output of Quality Gate stage."""
    article_md: str
    quality_report: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "article_md": self.article_md,
            "quality_report": self.quality_report,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QualityGateResult":
        return cls(
            article_md=data.get("article_md", ""),
            quality_report=data.get("quality_report", {}),
        )


@dataclass
class DraftMeta:
    """Metadata from drafting stage about claim usage and overlap handling."""
    used_allowed_claims: List[str] = field(default_factory=list)
    softened_claims_count: int = 0
    overlap_compressions: List[str] = field(default_factory=list)
    link_suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "used_allowed_claims": self.used_allowed_claims,
            "softened_claims_count": self.softened_claims_count,
            "overlap_compressions": self.overlap_compressions,
            "link_suggestions": self.link_suggestions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftMeta":
        return cls(
            used_allowed_claims=data.get("used_allowed_claims", []),
            softened_claims_count=data.get("softened_claims_count", 0),
            overlap_compressions=data.get("overlap_compressions", []),
            link_suggestions=data.get("link_suggestions", []),
        )


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
    competitor_analysis: Optional[Dict[str, Any]] = None
    eeat_signals: Optional[Dict[str, Any]] = None
    keyword_clusters: Optional["KeywordClusteringResult"] = None

    # v2 fields (EDI-82)
    claim_bank: Optional[ClaimBank] = None
    unique_angle: Optional[UniqueAngle] = None
    cluster_overlap_map: List[ClusterOverlapEntry] = field(default_factory=list)
    example_snippets: List[ExampleSnippet] = field(default_factory=list)
    terminology_canon: Optional[TerminologyCanon] = None

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
                    id=e.get("id", f"ex-{i}"),
                    example=e.get("example", e.get("text", "")),
                    why_it_matters=e.get("why_it_matters", ""),
                    source_id=e.get("source_id"),
                    confidence=e.get("confidence", "medium"),
                )
                for i, e in enumerate(data.get("examples", []))
            ],
            edge_cases=[
                EdgeCase(
                    id=ec.get("id", f"ec-{i}"),
                    case=ec.get("case", ""),
                    impact=ec.get("impact", ""),
                    source_id=ec.get("source_id"),
                    confidence=ec.get("confidence", "medium"),
                )
                for i, ec in enumerate(data.get("edge_cases", []))
            ],
            pitfalls_and_myths=[
                Pitfall(
                    id=p.get("id", f"p-{i}"),
                    item=p.get("item", ""),
                    why_wrong_or_risky=p.get("why_wrong_or_risky", ""),
                    source_id=p.get("source_id"),
                    confidence=p.get("confidence", "medium"),
                )
                for i, p in enumerate(data.get("pitfalls_and_myths", []))
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
            competitor_analysis=data.get("competitor_analysis"),
            eeat_signals=data.get("eeat_signals"),
            keyword_clusters=KeywordClusteringResult.from_dict(data["keyword_clusters"]) if data.get("keyword_clusters") else None,
            claim_bank=cls._parse_claim_bank(data.get("claim_bank")) if data.get("claim_bank") else None,
            unique_angle=cls._parse_unique_angle(data.get("unique_angle")) if data.get("unique_angle") else None,
            cluster_overlap_map=[
                ClusterOverlapEntry(
                    post_slug=e["post_slug"],
                    overlap_topics=e.get("overlap_topics", []),
                    avoid_sections=e.get("avoid_sections", []),
                    suggest_links=e.get("suggest_links", []),
                )
                for e in data.get("cluster_overlap_map", [])
            ],
            example_snippets=[
                ExampleSnippet(
                    scenario=es["scenario"],
                    snippet=es["snippet"],
                    where_to_use=es.get("where_to_use", "process"),
                    source_basis=es.get("source_basis", ""),
                )
                for es in data.get("example_snippets", [])
            ],
            terminology_canon=TerminologyCanon(
                terms=data["terminology_canon"].get("terms", {}),
                do_not_use=data["terminology_canon"].get("do_not_use", []),
            ) if data.get("terminology_canon") else None,
        )

    @staticmethod
    def _parse_claim_bank(data: dict) -> ClaimBank:
        claims = []
        for c in data.get("allowed_claims", []):
            ev_data = c.get("evidence", {})
            if isinstance(ev_data, str):
                ev = ClaimEvidence(source_title="", source_url="", supporting_quote_or_note=ev_data)
            else:
                ev = ClaimEvidence(
                    source_title=ev_data.get("source_title", ""),
                    source_url=ev_data.get("source_url", ""),
                    supporting_quote_or_note=ev_data.get("supporting_quote_or_note", ""),
                )
            claims.append(ClaimItem(
                claim_text=c["claim_text"],
                claim_type=c.get("claim_type", "definition"),
                evidence=ev,
                allowed_numeric=c.get("allowed_numeric", False),
                allowed_ranges=c.get("allowed_ranges"),
                use_rules=c.get("use_rules", ""),
            ))
        return ClaimBank(
            allowed_claims=claims,
            disallowed_claim_patterns=data.get("disallowed_claim_patterns", []),
        )

    @staticmethod
    def _parse_unique_angle(data: dict) -> UniqueAngle:
        return UniqueAngle(
            article_role=data.get("article_role", "cluster"),
            primary_intent=data.get("primary_intent", ""),
            differentiators=data.get("differentiators", []),
            must_not_cover=data.get("must_not_cover", []),
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
            "competitor_analysis": self.competitor_analysis,
            "eeat_signals": self.eeat_signals,
            "keyword_clusters": self.keyword_clusters.to_dict() if self.keyword_clusters else None,
            "claim_bank": {
                "allowed_claims": [
                    {
                        "claim_text": c.claim_text,
                        "claim_type": c.claim_type,
                        "evidence": {
                            "source_title": c.evidence.source_title,
                            "source_url": c.evidence.source_url,
                            "supporting_quote_or_note": c.evidence.supporting_quote_or_note,
                        },
                        "allowed_numeric": c.allowed_numeric,
                        "allowed_ranges": c.allowed_ranges,
                        "use_rules": c.use_rules,
                    }
                    for c in self.claim_bank.allowed_claims
                ],
                "disallowed_claim_patterns": self.claim_bank.disallowed_claim_patterns,
            } if self.claim_bank else None,
            "unique_angle": {
                "article_role": self.unique_angle.article_role,
                "primary_intent": self.unique_angle.primary_intent,
                "differentiators": self.unique_angle.differentiators,
                "must_not_cover": self.unique_angle.must_not_cover,
            } if self.unique_angle else None,
            "cluster_overlap_map": [
                {
                    "post_slug": e.post_slug,
                    "overlap_topics": e.overlap_topics,
                    "avoid_sections": e.avoid_sections,
                    "suggest_links": e.suggest_links,
                }
                for e in self.cluster_overlap_map
            ],
            "example_snippets": [
                {
                    "scenario": es.scenario,
                    "snippet": es.snippet,
                    "where_to_use": es.where_to_use,
                    "source_basis": es.source_basis,
                }
                for es in self.example_snippets
            ],
            "terminology_canon": {
                "terms": self.terminology_canon.terms,
                "do_not_use": self.terminology_canon.do_not_use,
            } if self.terminology_canon else None,
        }


# =============================================================================
# Keyword Clustering Contract
# =============================================================================

@dataclass
class KeywordCluster:
    """A cluster of semantically related keywords."""
    cluster_name: str
    cluster_intent: str
    primary_keyword: str
    keywords: List[str]
    total_volume: int  # 0 if metrics unavailable
    suggested_section_topic: str

    def to_dict(self) -> dict:
        return {
            "cluster_name": self.cluster_name,
            "cluster_intent": self.cluster_intent,
            "primary_keyword": self.primary_keyword,
            "keywords": self.keywords,
            "total_volume": self.total_volume,
            "suggested_section_topic": self.suggested_section_topic,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KeywordCluster":
        return cls(
            cluster_name=data["cluster_name"],
            cluster_intent=data["cluster_intent"],
            primary_keyword=data["primary_keyword"],
            keywords=data["keywords"],
            total_volume=data.get("total_volume", 0),
            suggested_section_topic=data["suggested_section_topic"],
        )


@dataclass
class KeywordClusteringResult:
    """Result of keyword clustering sub-stage."""
    primary_cluster: KeywordCluster
    secondary_clusters: List[KeywordCluster]
    unclustered: List[str]

    def to_dict(self) -> dict:
        return {
            "primary_cluster": self.primary_cluster.to_dict(),
            "secondary_clusters": [c.to_dict() for c in self.secondary_clusters],
            "unclustered": self.unclustered,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KeywordClusteringResult":
        return cls(
            primary_cluster=KeywordCluster.from_dict(data["primary_cluster"]),
            secondary_clusters=[KeywordCluster.from_dict(c) for c in data.get("secondary_clusters", [])],
            unclustered=data.get("unclustered", []),
        )


# =============================================================================
# Cluster Planner Contracts
# =============================================================================

@dataclass
class ArticleBrief:
    """
    Brief for a single article within a cluster plan.

    Generated by ClusterPlanner, consumed by PipelineRunner.
    """
    title_candidate: str
    role: str  # "pillar" | "cluster" | "supporting"
    primary_intent: str  # "informational" | "transactional" | "commercial" | "navigational"
    topic_boundaries: Dict[str, List[str]]  # {"in_scope": [...], "out_of_scope": [...]}
    must_answer_questions: List[str]  # 5-10 questions
    target_terms: List[str]  # 10-30 keywords
    unique_angle: Dict[str, Any]  # {"differentiators": [...], "must_not_cover": [...]}
    internal_links_plan: List[Dict[str, str]]  # [{"target_slug": ..., "anchor_hint": ...}]
    seed_queries: List[str]  # queries for research stage
    estimated_volume: int = 0
    priority: int = 1  # 1=highest

    def to_dict(self) -> dict:
        return {
            "title_candidate": self.title_candidate,
            "role": self.role,
            "primary_intent": self.primary_intent,
            "topic_boundaries": self.topic_boundaries,
            "must_answer_questions": self.must_answer_questions,
            "target_terms": self.target_terms,
            "unique_angle": self.unique_angle,
            "internal_links_plan": self.internal_links_plan,
            "seed_queries": self.seed_queries,
            "estimated_volume": self.estimated_volume,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArticleBrief":
        return cls(
            title_candidate=data["title_candidate"],
            role=data.get("role", "cluster"),
            primary_intent=data.get("primary_intent", "informational"),
            topic_boundaries=data.get("topic_boundaries", {"in_scope": [], "out_of_scope": []}),
            must_answer_questions=data.get("must_answer_questions", []),
            target_terms=data.get("target_terms", []),
            unique_angle=data.get("unique_angle", {"differentiators": [], "must_not_cover": []}),
            internal_links_plan=data.get("internal_links_plan", []),
            seed_queries=data.get("seed_queries", []),
            estimated_volume=data.get("estimated_volume", 0),
            priority=data.get("priority", 1),
        )


@dataclass
class ClusterPlan:
    """
    Output of ClusterPlanner.plan() — a set of articles to write for a topic cluster.
    """
    big_topic: str
    region: str
    pillar: ArticleBrief
    cluster_articles: List[ArticleBrief]
    generated_at: str  # ISO-8601
    discovered_keywords: List[Dict[str, Any]] = field(default_factory=list)  # [{keyword, volume, cpc, competition}]

    def to_dict(self) -> dict:
        return {
            "big_topic": self.big_topic,
            "region": self.region,
            "pillar": self.pillar.to_dict(),
            "cluster_articles": [a.to_dict() for a in self.cluster_articles],
            "generated_at": self.generated_at,
            "discovered_keywords": self.discovered_keywords,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClusterPlan":
        return cls(
            big_topic=data["big_topic"],
            region=data.get("region", "ru"),
            pillar=ArticleBrief.from_dict(data["pillar"]),
            cluster_articles=[ArticleBrief.from_dict(a) for a in data.get("cluster_articles", [])],
            generated_at=data.get("generated_at", ""),
            discovered_keywords=data.get("discovered_keywords", []),
        )

    @property
    def all_articles(self) -> List[ArticleBrief]:
        return [self.pillar] + self.cluster_articles


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
    eeat_plan: Optional[List[Dict[str, Any]]] = None

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
                type=cb.get("type", "paragraph"),
                goal=cb.get("goal", ""),
                source_refs=parse_source_refs(cb.get("source_refs", {})),
            )

        def parse_subsection(ss: dict) -> Subsection:
            return Subsection(
                id=ss.get("id", ""),
                h3=ss.get("h3", ""),
                purpose=ss.get("purpose", ""),
                word_count_target=ss.get("word_count_target", 300),
                must_answer_questions=ss.get("must_answer_questions", []),
                content_blocks=[parse_content_block(cb) for cb in ss.get("content_blocks", [])],
            )

        def parse_section(s: dict) -> Section:
            return Section(
                id=s.get("id", ""),
                h2=s.get("h2", ""),
                purpose=s.get("purpose", ""),
                word_count_target=s.get("word_count_target", 500),
                must_answer_questions=s.get("must_answer_questions", []),
                content_blocks=[parse_content_block(cb) for cb in s.get("content_blocks", [])],
                subsections=[parse_subsection(ss) for ss in s.get("subsections", [])],
            )

        # Defensive parsing — LLM may omit optional sections
        intro_data = data.get("introduction", {})
        conclusion_data = data.get("conclusion", {})
        coverage_data = data.get("coverage_check", {})

        return cls(
            title=data.get("title", "Untitled"),
            subtitle=data.get("subtitle", ""),
            target_total_words=data.get("target_total_words", 2000),
            introduction=Introduction(
                purpose=intro_data.get("purpose", "Introduce the topic"),
                key_points=intro_data.get("key_points", []),
                word_count_target=intro_data.get("word_count_target", 200),
            ),
            sections=[parse_section(s) for s in data.get("sections", [])],
            conclusion=Conclusion(
                purpose=conclusion_data.get("purpose", "Summarize key takeaways"),
                takeaways=conclusion_data.get("takeaways", []),
                word_count_target=conclusion_data.get("word_count_target", 200),
            ),
            coverage_check=CoverageCheck(
                all_must_answer_covered=coverage_data.get("all_must_answer_covered", True),
                uncovered_questions=coverage_data.get("uncovered_questions", []),
                missing_notes=coverage_data.get("missing_notes"),
            ),
            eeat_plan=data.get("eeat_plan"),
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
            "eeat_plan": self.eeat_plan,
        }


# =============================================================================
# SEO Polish Stage Contracts
# =============================================================================

@dataclass
class SeoCheckResult:
    """Result of a single SEO check."""
    check: str       # "keyword_density", "keyword_in_h1", etc.
    status: str      # "pass" | "fail" | "warning"
    value: Any       # current value (density float, bool, count int)
    threshold: Any   # expected value
    details: str     # human-readable description

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SeoCheckResult":
        return cls(
            check=data["check"],
            status=data["status"],
            value=data["value"],
            threshold=data["threshold"],
            details=data["details"],
        )


@dataclass
class SeoAnalysis:
    """Result of programmatic SEO analysis."""
    checks: List[SeoCheckResult]
    needs_fix: bool
    keyword_density: float
    keywords_found: Dict[str, int]  # keyword -> count

    @property
    def failed_checks(self) -> List[SeoCheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warning_checks(self) -> List[SeoCheckResult]:
        return [c for c in self.checks if c.status == "warning"]

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "needs_fix": self.needs_fix,
            "keyword_density": self.keyword_density,
            "keywords_found": self.keywords_found,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SeoAnalysis":
        return cls(
            checks=[SeoCheckResult.from_dict(c) for c in data["checks"]],
            needs_fix=data["needs_fix"],
            keyword_density=data["keyword_density"],
            keywords_found=data["keywords_found"],
        )


@dataclass
class SeoPolishResult:
    """Output of SEO Polish stage."""
    analysis_before: SeoAnalysis
    analysis_after: Optional[SeoAnalysis]  # None if no fixes needed
    llm_called: bool
    changes_made: List[str]
    tokens_used: int

    def to_dict(self) -> dict:
        return {
            "analysis_before": self.analysis_before.to_dict(),
            "analysis_after": self.analysis_after.to_dict() if self.analysis_after else None,
            "llm_called": self.llm_called,
            "changes_made": self.changes_made,
            "tokens_used": self.tokens_used,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SeoPolishResult":
        return cls(
            analysis_before=SeoAnalysis.from_dict(data["analysis_before"]),
            analysis_after=SeoAnalysis.from_dict(data["analysis_after"]) if data.get("analysis_after") else None,
            llm_called=data["llm_called"],
            changes_made=data["changes_made"],
            tokens_used=data["tokens_used"],
        )


# =============================================================================
# Meta Stage Contracts
# =============================================================================

@dataclass
class MetaResult:
    """
    Output of Meta stage.

    SEO metadata generated from the finished article.
    """
    meta_title: str  # ≤60 chars, contains target keyword
    meta_description: str  # ≤160 chars, contains keyword + CTA
    slug: str  # lowercase, hyphens, 3-5 words
    schema_json_ld: Optional[str] = None  # JSON-LD structured data

    @classmethod
    def from_dict(cls, data: dict) -> "MetaResult":
        return cls(
            meta_title=data["meta_title"],
            meta_description=data["meta_description"],
            slug=data["slug"],
            schema_json_ld=data.get("schema_json_ld"),
        )

    def to_dict(self) -> dict:
        return {
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
            "slug": self.slug,
            "schema_json_ld": self.schema_json_ld,
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

    # SEO metadata
    meta: Optional[MetaResult] = None

    # Cover image
    cover_image_url: str = ""
    cover_image_alt: str = ""

    # Intermediate results (for debugging/logging)
    intent: IntentResult = None
    research: ResearchResult = None
    outline: OutlineResult = None
    draft_md: str = ""

    # Internal linking data (for post-publication registration)
    linking_data: Optional[Dict[str, Any]] = None

    # Token usage
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    stage_tokens: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Metadata
    started_at: str = ""
    completed_at: str = ""
    stages_completed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        result = {
            "topic": self.topic,
            "region": self.region,
            "article_md": self.article_md,
            "title": self.title,
            "subtitle": self.subtitle,
            "word_count": self.word_count,
            "cover_image_url": self.cover_image_url,
            "cover_image_alt": self.cover_image_alt,
            "linking_data": self.linking_data,
            "meta": self.meta.to_dict() if self.meta else None,
            "intent": self.intent.to_dict() if self.intent else None,
            "research": self.research.to_dict() if self.research else None,
            "outline": self.outline.to_dict() if self.outline else None,
            "draft_md": self.draft_md,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "stage_tokens": self.stage_tokens,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stages_completed": self.stages_completed,
        }
        return result
