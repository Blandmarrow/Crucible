from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class SavedRunRecipe(Base):
    """A named run recipe in the library (M6 §6): which stages run and each one's
    settings, saved from the Runs page's start form.

    Deliberately global, with no dataset or plan foreign key, like the prompt library:
    a recipe describes a process, not a dataset. The stored recipe is a
    `schemas/run.RunRecipeDraft`, which may lack the fields only this machine can fill
    (an export folder, a provider), so loading one into the form can need finishing.
    It keeps this machine's provider ids and export folder; the JSON export strips them
    (`services/run_recipe_service.py`). It never holds an API key or a plan id.
    """

    __tablename__ = "run_recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The name folded for uniqueness: case and runs of spaces do not make a second recipe.
    name_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    recipe: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
