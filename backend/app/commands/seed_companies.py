# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""Seed database with company identification patterns."""

import asyncio
import json
from pathlib import Path

import click

from sqlalchemy import select

from app.commands import command, info, success, warning


@command("seed-companies", help="Seed database with company identification patterns")
@click.option("--clear", is_flag=True, help="Clear existing companies before seeding")
@click.option("--dry-run", is_flag=True, help="Show what would be created without making changes")
def seed_companies(clear: bool, dry_run: bool) -> None:
    """Seed the database with company data for message classification.

    Loads company data from app/data/companies.json.

    Example:
        project cmd seed-companies
        project cmd seed-companies --clear
        project cmd seed-companies --dry-run
    """
    data_path = Path(__file__).parent.parent / "data" / "companies.json"

    if not data_path.exists():
        warning(f"Companies data file not found: {data_path}")
        return

    with open(data_path, encoding="utf-8") as f:
        companies_data = json.load(f)

    if dry_run:
        info(f"[DRY RUN] Would create {len(companies_data)} companies")
        if clear:
            info("[DRY RUN] Would clear existing companies first")
        for c in companies_data:
            pattern_count = sum(
                len(g.get("patterns", [])) for g in c.get("identification_patterns", [])
            )
            info(f"  - {c['name']} ({c.get('display_name', 'N/A')}) — {pattern_count} patterns")
        return

    async def _seed():
        from app.db.session import async_session_maker
        from app.db.models.company import Company
        from app.repositories import company_repo

        async with async_session_maker() as session:
            if clear:
                info("Clearing existing companies...")
                count = await company_repo.clear_all(session)
                await session.commit()
                info(f"Cleared {count} companies")

            # Check existing
            result = await session.execute(select(Company).limit(1))
            existing = result.scalars().first()

            if existing and not clear:
                info("Companies already exist. Use --clear to replace them.")
                return

            info(f"Creating {len(companies_data)} companies...")
            created = await company_repo.bulk_create(session, companies_data)
            await session.commit()
            success(f"Created {len(created)} companies")

    asyncio.run(_seed())
