"""
Validation Pipeline - orchestrates all validation checks.
"""

from uuid import UUID
from typing import Optional
from dataclasses import dataclass

from src.db.session import SessionLocal
from src.db import models
from src.services.validators.seo_lint import SEOLintValidator
from src.services.validators.plagiarism import PlagiarismValidator


@dataclass
class ValidationResult:
    overall_status: str  # passed | warning | failed
    overall_score: float
    seo_lint: Optional[dict] = None
    plagiarism: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status,
            "overall_score": self.overall_score,
            "checks": {
                "seo_lint": self.seo_lint,
                "plagiarism": self.plagiarism,
            },
        }


class ValidationPipeline:
    """
    Orchestrates validation checks for drafts.

    Runs:
    1. SEO Lint - basic SEO requirements
    2. Plagiarism - similarity with competitors (if URLs available)
    """

    def __init__(self):
        self.seo_validator = SEOLintValidator()
        self.plagiarism_validator = PlagiarismValidator()

    async def run(self, draft_id: UUID) -> ValidationResult:
        """
        Run all validation checks on a draft.

        Updates draft status and validation_report in DB.
        Returns ValidationResult.
        """
        db = SessionLocal()
        try:
            # Load draft
            draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if not draft:
                raise ValueError(f"Draft not found: {draft_id}")

            # Load brief if exists (for keywords and competitor URLs)
            brief = None
            if draft.brief_id:
                brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()

            # Update status to validating
            draft.status = "validating"
            db.commit()

            # Get parameters from brief or use defaults
            target_keyword = ""
            word_count_min = 1500
            word_count_max = 5000
            competitor_urls = []

            if brief:
                target_keyword = brief.target_keyword or ""
                word_count_min = brief.word_count_min or 1500
                word_count_max = brief.word_count_max or 2500
                competitor_urls = brief.competitor_urls or []
            elif draft.keywords:
                target_keyword = draft.keywords[0] if draft.keywords else ""

            # Run SEO Lint (use meta_title for title checks, fall back to title)
            seo_report = self.seo_validator.validate(
                content_md=draft.content_md or "",
                title=draft.meta_title or draft.title or "",
                meta_description=draft.meta_description,
                target_keyword=target_keyword,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
            )

            # Run Plagiarism Check (if competitor URLs available)
            plagiarism_report = None
            if competitor_urls:
                plagiarism_report = await self.plagiarism_validator.validate(
                    content=draft.content_md or "",
                    competitor_urls=competitor_urls,
                )

            # Calculate overall status and score
            statuses = [seo_report.status]
            scores = [seo_report.score]

            if plagiarism_report:
                statuses.append(plagiarism_report.status)
                scores.append(plagiarism_report.score)

            # Overall status: worst of all checks
            if "failed" in statuses:
                overall_status = "failed"
            elif "warning" in statuses:
                overall_status = "warning"
            else:
                overall_status = "passed"

            overall_score = sum(scores) / len(scores) if scores else 0

            # Build result
            result = ValidationResult(
                overall_status=overall_status,
                overall_score=round(overall_score, 1),
                seo_lint=seo_report.to_dict(),
                plagiarism=plagiarism_report.to_dict() if plagiarism_report else None,
            )

            # Update draft
            draft.validation_score = overall_score
            draft.validation_report = result.to_dict()

            # Validation is informational — never block publishing
            draft.status = "validated"

            db.commit()

            return result

        except Exception as e:
            # On error, still allow proceeding
            if db.is_active:
                draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if draft:
                    draft.status = "validated"
                    draft.validation_report = {"error": str(e)}
                    db.commit()
            raise

        finally:
            db.close()

    def run_sync(self, draft_id: UUID) -> ValidationResult:
        """
        Synchronous version for non-async contexts.
        Skips plagiarism check (requires async HTTP).
        """
        db = SessionLocal()
        try:
            draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if not draft:
                raise ValueError(f"Draft not found: {draft_id}")

            brief = None
            if draft.brief_id:
                brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()

            draft.status = "validating"
            db.commit()

            # Get parameters
            target_keyword = ""
            word_count_min = 1500
            word_count_max = 5000

            if brief:
                target_keyword = brief.target_keyword or ""
                word_count_min = brief.word_count_min or 1500
                word_count_max = brief.word_count_max or 5000
            elif draft.keywords:
                target_keyword = draft.keywords[0] if draft.keywords else ""

            # Run SEO Lint (use meta_title for title checks, fall back to title)
            seo_report = self.seo_validator.validate(
                content_md=draft.content_md or "",
                title=draft.meta_title or draft.title or "",
                meta_description=draft.meta_description,
                target_keyword=target_keyword,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
            )

            # Determine overall status (only SEO for sync)
            overall_status = seo_report.status
            overall_score = seo_report.score

            result = ValidationResult(
                overall_status=overall_status,
                overall_score=round(overall_score, 1),
                seo_lint=seo_report.to_dict(),
                plagiarism=None,  # Skipped in sync mode
            )

            draft.validation_score = overall_score
            draft.validation_report = result.to_dict()

            # Validation is informational — never block publishing
            draft.status = "validated"

            db.commit()

            return result

        except Exception as e:
            if db.is_active:
                draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if draft:
                    draft.status = "validated"
                    draft.validation_report = {"error": str(e)}
                    db.commit()
            raise

        finally:
            db.close()
