import uuid
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Uuid

db = SQLAlchemy()


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    filename = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.Text, nullable=False)
    total_pages = db.Column(db.Integer)
    status = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pages = db.relationship("Page", backref="document", cascade="all, delete-orphan")
    jobs = db.relationship("Job", backref="document", cascade="all, delete-orphan")


class Page(db.Model):
    __tablename__ = "pages"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = db.Column(Uuid, db.ForeignKey("documents.id"), nullable=False)
    page_number = db.Column(db.Integer)
    image_path = db.Column(db.Text)
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    status = db.Column(db.Text, default="pending")

    problems = db.relationship("Problem", backref="page", cascade="all, delete-orphan")


class Problem(db.Model):
    __tablename__ = "problems"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    page_id = db.Column(Uuid, db.ForeignKey("pages.id"), nullable=False)
    order_index = db.Column(db.Integer)
    x = db.Column(db.Integer)
    y = db.Column(db.Integer)
    w = db.Column(db.Integer)
    h = db.Column(db.Integer)
    crop_path = db.Column(db.Text)
    status = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviews = db.relationship("Review", backref="problem", cascade="all, delete-orphan")
    tags = db.relationship("Tag", secondary="problem_tags", backref="problems")
    contents = db.relationship("ProblemContent", backref="problem", cascade="all, delete-orphan")


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    problem_id = db.Column(Uuid, db.ForeignKey("problems.id"), nullable=False)
    checked = db.Column(db.Boolean, default=False)
    edited = db.Column(db.Boolean, default=False)
    reviewer = db.Column(db.Text)
    reviewed_at = db.Column(db.DateTime)


class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.Text, nullable=False)


class ProblemTag(db.Model):
    __tablename__ = "problem_tags"

    problem_id = db.Column(Uuid, db.ForeignKey("problems.id"), primary_key=True)
    tag_id = db.Column(Uuid, db.ForeignKey("tags.id"), primary_key=True)


class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = db.Column(Uuid, db.ForeignKey("documents.id"), nullable=False)
    type = db.Column(db.Text)
    status = db.Column(db.Text)
    progress = db.Column(db.Integer)
    current_page = db.Column(db.Integer)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class ProblemContent(db.Model):
    __tablename__ = "problem_contents"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    problem_id = db.Column(Uuid, db.ForeignKey("problems.id"), nullable=False)
    # Self-referential: set when this row is nested inside a type="group" row
    # (e.g. a <보기> box). Top-level content (most rows) leaves this null.
    parent_content_id = db.Column(Uuid, db.ForeignKey("problem_contents.id"), nullable=True)
    order_index = db.Column(db.Integer)
    type = db.Column(db.Text)
    # Visible marker for a structured item: "①", "ㄱ", "(가)", or a group's
    # own heading like "보기". Null for plain stem text/formula rows.
    label = db.Column(db.Text)
    content = db.Column(db.Text)
    bbox_x = db.Column(db.Integer)
    bbox_y = db.Column(db.Integer)
    bbox_w = db.Column(db.Integer)
    bbox_h = db.Column(db.Integer)
    confidence = db.Column(db.Float)
