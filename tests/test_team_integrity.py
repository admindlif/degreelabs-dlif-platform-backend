from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.cohort import Cohort, CohortStatus
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.program import Program
from app.models.team import Team, TeamMembership, TeamMemberRole
from app.models.user import AccountStatus, User, UserRole
from app.services.team import (
    add_member_to_team,
    remove_member_from_team,
    set_team_lead,
)


def create_program(db: Session) -> Program:
    program = Program(
        name=f"DLIF Test {uuid4().hex[:6]}",
        code=f"DLIF-{uuid4().hex[:8]}",
        is_active=True,
    )
    db.add(program)
    db.commit()
    db.refresh(program)
    return program


def create_cohort(
    db: Session,
    program: Program,
    suffix: str,
) -> Cohort:
    cohort = Cohort(
        program_id=program.id,
        name=f"Test Cohort {suffix}",
        code=f"TEST-{suffix}-{uuid4().hex[:6]}",
        status=CohortStatus.ACTIVE,
    )
    db.add(cohort)
    db.commit()
    db.refresh(cohort)
    return cohort


def create_fellow(
    db: Session,
    prefix: str = "fellow",
) -> User:
    user = User(
        first_name="Test",
        last_name="Fellow",
        email=f"{prefix}_{uuid4().hex[:8]}@degreelabs.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.FELLOW,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_admin(db: Session) -> User:
    user = User(
        first_name="Test",
        last_name="Admin",
        email=f"admin_{uuid4().hex[:8]}@degreelabs.com",
        password_hash=hash_password("Pass12345!"),
        role=UserRole.ADMIN,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def enroll_fellow(
    db: Session,
    fellow: User,
    cohort: Cohort,
) -> Enrollment:
    enrollment = Enrollment(
        user_id=fellow.id,
        cohort_id=cohort.id,
        enrollment_status=EnrollmentStatus.ACTIVE,
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


def create_team(
    db: Session,
    cohort: Cohort,
    name: str,
) -> Team:
    team = Team(
        cohort_id=cohort.id,
        name=f"{name}-{uuid4().hex[:5]}",
        is_active=True,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def cleanup(
    db: Session,
    *,
    users=None,
    programs=None,
):
    """
    Programs cascade-delete Cohorts, Teams and Enrollments.
    Users are deleted separately.
    """
    db.rollback()

    for user in users or []:
        existing = db.get(User, user.id)
        if existing:
            db.delete(existing)

    for program in programs or []:
        existing = db.get(Program, program.id)
        if existing:
            db.delete(existing)

    db.commit()


def test_fellow_can_join_team_in_same_cohort(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")
    fellow = create_fellow(db)
    enroll_fellow(db, fellow, cohort)
    team = create_team(db, cohort, "Alpha")

    try:
        membership = add_member_to_team(
            db=db,
            team_id=team.id,
            user_id=fellow.id,
            team_role=TeamMemberRole.MEMBER,
        )

        assert membership.user_id == fellow.id
        assert membership.team_id == team.id
        assert membership.cohort_id == cohort.id
        assert membership.team_role == TeamMemberRole.MEMBER

    finally:
        cleanup(
            db,
            users=[fellow],
            programs=[program],
        )


def test_fellow_cannot_join_team_in_different_cohort(
    db: Session,
):
    program = create_program(db)

    cohort_a = create_cohort(
        db,
        program,
        "A",
    )

    cohort_b = create_cohort(
        db,
        program,
        "B",
    )

    fellow = create_fellow(db)

    enroll_fellow(
        db,
        fellow,
        cohort_a,
    )

    team_b = create_team(
        db,
        cohort_b,
        "Beta",
    )

    try:
        with pytest.raises(HTTPException) as exc:
            add_member_to_team(
                db=db,
                team_id=team_b.id,
                user_id=fellow.id,
            )

        assert exc.value.status_code == 409
        assert "same Cohort" in exc.value.detail

    finally:
        cleanup(
            db,
            users=[fellow],
            programs=[program],
        )


def test_fellow_cannot_join_two_teams_same_cohort(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")
    fellow = create_fellow(db)

    enroll_fellow(
        db,
        fellow,
        cohort,
    )

    team_a = create_team(
        db,
        cohort,
        "Alpha",
    )

    team_b = create_team(
        db,
        cohort,
        "Beta",
    )

    try:
        add_member_to_team(
            db=db,
            team_id=team_a.id,
            user_id=fellow.id,
        )

        with pytest.raises(HTTPException) as exc:
            add_member_to_team(
                db=db,
                team_id=team_b.id,
                user_id=fellow.id,
            )

        assert exc.value.status_code == 409
        assert "another Team" in exc.value.detail

    finally:
        cleanup(
            db,
            users=[fellow],
            programs=[program],
        )


def test_team_cannot_have_two_leads(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")

    fellow_a = create_fellow(db, "lead_a")
    fellow_b = create_fellow(db, "lead_b")

    enroll_fellow(db, fellow_a, cohort)
    enroll_fellow(db, fellow_b, cohort)

    team = create_team(
        db,
        cohort,
        "Alpha",
    )

    try:
        add_member_to_team(
            db=db,
            team_id=team.id,
            user_id=fellow_a.id,
            team_role=TeamMemberRole.LEAD,
        )

        with pytest.raises(HTTPException) as exc:
            add_member_to_team(
                db=db,
                team_id=team.id,
                user_id=fellow_b.id,
                team_role=TeamMemberRole.LEAD,
            )

        assert exc.value.status_code == 409
        assert "already has a Team Lead" in exc.value.detail

    finally:
        cleanup(
            db,
            users=[fellow_a, fellow_b],
            programs=[program],
        )


def test_change_team_lead(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")

    fellow_a = create_fellow(db, "lead_a")
    fellow_b = create_fellow(db, "lead_b")

    enroll_fellow(db, fellow_a, cohort)
    enroll_fellow(db, fellow_b, cohort)

    team = create_team(
        db,
        cohort,
        "Alpha",
    )

    try:
        add_member_to_team(
            db=db,
            team_id=team.id,
            user_id=fellow_a.id,
            team_role=TeamMemberRole.LEAD,
        )

        add_member_to_team(
            db=db,
            team_id=team.id,
            user_id=fellow_b.id,
            team_role=TeamMemberRole.MEMBER,
        )

        new_lead = set_team_lead(
            db=db,
            team_id=team.id,
            user_id=fellow_b.id,
        )

        assert new_lead.team_role == TeamMemberRole.LEAD

        old_lead = (
            db.query(TeamMembership)
            .filter(
                TeamMembership.team_id == team.id,
                TeamMembership.user_id == fellow_a.id,
            )
            .one()
        )

        assert old_lead.team_role == TeamMemberRole.MEMBER

        leads = (
            db.query(TeamMembership)
            .filter(
                TeamMembership.team_id == team.id,
                TeamMembership.team_role == TeamMemberRole.LEAD,
            )
            .all()
        )

        assert len(leads) == 1
        assert leads[0].user_id == fellow_b.id

    finally:
        cleanup(
            db,
            users=[fellow_a, fellow_b],
            programs=[program],
        )


def test_active_team_lead_cannot_be_removed(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")
    fellow = create_fellow(db)

    enroll_fellow(
        db,
        fellow,
        cohort,
    )

    team = create_team(
        db,
        cohort,
        "Alpha",
    )

    try:
        add_member_to_team(
            db=db,
            team_id=team.id,
            user_id=fellow.id,
            team_role=TeamMemberRole.LEAD,
        )

        with pytest.raises(HTTPException) as exc:
            remove_member_from_team(
                db=db,
                team_id=team.id,
                user_id=fellow.id,
            )

        assert exc.value.status_code == 409
        assert "Assign another Team Lead" in exc.value.detail

    finally:
        cleanup(
            db,
            users=[fellow],
            programs=[program],
        )


def test_non_fellow_cannot_join_team(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")
    admin = create_admin(db)

    team = create_team(
        db,
        cohort,
        "Alpha",
    )

    try:
        with pytest.raises(HTTPException) as exc:
            add_member_to_team(
                db=db,
                team_id=team.id,
                user_id=admin.id,
            )

        assert exc.value.status_code == 404

    finally:
        cleanup(
            db,
            users=[admin],
            programs=[program],
        )


def test_database_prevents_two_active_cohorts_for_same_fellow(
    db: Session,
):
    program = create_program(db)

    cohort_a = create_cohort(
        db,
        program,
        "A",
    )

    cohort_b = create_cohort(
        db,
        program,
        "B",
    )

    fellow = create_fellow(db)

    try:
        first = Enrollment(
            user_id=fellow.id,
            cohort_id=cohort_a.id,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )

        db.add(first)
        db.commit()

        second = Enrollment(
            user_id=fellow.id,
            cohort_id=cohort_b.id,
            enrollment_status=EnrollmentStatus.ACTIVE,
        )

        db.add(second)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    finally:
        cleanup(
            db,
            users=[fellow],
            programs=[program],
        )


def test_database_prevents_two_team_leads(
    db: Session,
):
    program = create_program(db)
    cohort = create_cohort(db, program, "A")

    fellow_a = create_fellow(db, "lead_a")
    fellow_b = create_fellow(db, "lead_b")

    enroll_fellow(db, fellow_a, cohort)
    enroll_fellow(db, fellow_b, cohort)

    team = create_team(
        db,
        cohort,
        "Alpha",
    )

    try:
        first = TeamMembership(
            team_id=team.id,
            cohort_id=cohort.id,
            user_id=fellow_a.id,
            team_role=TeamMemberRole.LEAD,
        )

        db.add(first)
        db.commit()

        second = TeamMembership(
            team_id=team.id,
            cohort_id=cohort.id,
            user_id=fellow_b.id,
            team_role=TeamMemberRole.LEAD,
        )

        db.add(second)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    finally:
        cleanup(
            db,
            users=[fellow_a, fellow_b],
            programs=[program],
        )