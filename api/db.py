from typing import Any, List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor
from pypika.enums import Order
from .models import Course, CourseSection, CourseSectionPeriod

from pypika import Query, Table, Field
from pypika.queries import QueryBuilder

import os
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

course_sections_t = Table("course_sections")
course_sections_q: QueryBuilder = (
    Query.from_(course_sections_t)
    .orderby(course_sections_t.course_subject_prefix)
    .orderby(course_sections_t.course_number)
)  # .orderby(course_sections_t.section_id) \

periods_t = Table("course_section_periods")
periods_q: QueryBuilder = Query.from_(periods_t).select("*")


conn = psycopg2.connect(os.environ["POSTGRES_DSN"], cursor_factory=RealDictCursor)


def update_course_sections(semester_id: str, course_sections: List[CourseSection]):
    c = conn.cursor()

    c.execute("DELETE FROM course_section_periods")
    c.execute("DELETE FROM course_sections")
    # # c.execute(
    # #     'UPDATE course_sections SET removed=true WHERE semester_id=%s', (semester_id,))
    # c.execute('SELECT crn FROM course_sections WHERE semester_id=%s',
    #           (semester_id,))

    # existing_crns = c.fetchall()

    for course_section in course_sections:
        record = course_section.to_record()

        # Add new record
        placeholders = ",".join(map(lambda key: f"%({key})s", record.keys()))
        c.execute(
            f'INSERT INTO course_sections({",".join(record.keys())}) VALUES ({placeholders})',
            record,
        )
        print(f'Added new course_section {record["crn"]}')

        # Add course sections
        for period in course_section.periods:
            record = period.to_record()
            placeholders = ",".join(map(lambda key: f"%({key})s", record.keys()))
            c.execute(
                f'INSERT INTO course_section_periods({",".join(record.keys())}) VALUES ({placeholders})',
                record,
            )
            print(f"Added {period.class_type} period for {course_section.crn}")
    conn.commit()


def fetch_course_sections(semester_id: str, crns: List[str]) -> CourseSection:
    c = conn.cursor()

    # Create query to fetch course sections
    q: QueryBuilder = (
        course_sections_q.select("*")
        .where(course_sections_t.semester_id == semester_id)
        .where(course_sections_t.crn.isin(crns))
    )

    c.execute(q.get_sql())
    course_section_records = c.fetchall()

    # BIG BRAIN MOVE:
    # Instead of making a separate query for each section's periods, fetch them all first and them associate them with their section
    q: QueryBuilder = periods_q.where(periods_t.semester_id == semester_id).where(
        periods_t.crn.isin(crns)
    )

    c.execute(q.get_sql())
    period_records = c.fetchall()

    # Match the periods fetched to their course section records!
    sections = []
    for record in course_section_records:
        # Find period records for this course section
        section_period_records = filter(
            lambda pr: pr["crn"] == record["crn"], period_records
        )
        # Turn those period records into CourseSectionPeriods
        periods = list(map(CourseSectionPeriod.from_record, section_period_records))
        # Add created CourseSection
        sections.append(CourseSection.from_record(record, periods))

    return sections


def search_course_sections(semester_id: str, limit: int, offset: int, **search):
    c = conn.cursor()

    q: QueryBuilder = (
        course_sections_q.select("*")
        .where(course_sections_t.semester_id == semester_id)
        .limit(limit)
        .offset(offset)
    )

    # Values that require exact matches
    for col in ["course_number", "course_subject_prefix"]:
        if search[col]:
            q = q.where(course_sections_t[col] == search[col])

    # Values that require wildcards
    for col in ["course_title"]:
        if search[col]:
            q = q.where(course_sections_t[col].ilike(f"%{search[col]}%"))

    # Special values that require complex checks
    if search["has_seats"] == False:
        q = q.where(course_sections_t.enrollments >= course_sections_t.max_enrollments)

    if search["has_seats"] == True:
        q = q.where(course_sections_t.enrollments < course_sections_t.max_enrollments)

    c.execute(q.get_sql())
    records = c.fetchall()

    return records_to_sections(semester_id, records)


def fetch_course_section_periods(
    semester_id: str, crn: str
) -> List[CourseSectionPeriod]:
    c = conn.cursor()
    c.execute(
        "SELECT * FROM course_section_periods WHERE semester_id=%s and crn=%s",
        (semester_id, crn),
    )
    course_section_periods_raw = c.fetchall()

    return list(map(CourseSectionPeriod.from_record, course_section_periods_raw))


def fetch_courses_with_sections(
    semester_id: str, limit: int, offset: int, **search
) -> List[Course]:
    c = conn.cursor()

    print("Start...")
    # Fetch course sections then manually aggregate them with sections and periods
    q: QueryBuilder = (
        course_sections_q.select("*")
        .where(course_sections_t.semester_id == semester_id)
        .orderby("section_id")
        .limit(limit)
        .offset(offset)
    )

    c.execute(q.get_sql())
    records = c.fetchall()
    print("Fetched!")
    courses = dict()
    for record in records:
        key = (
            record["course_subject_prefix"],
            record["course_number"],
            record["course_title"],
        )

        if key not in courses:
            courses[key] = Course(
                semester_id=semester_id,
                subject_prefix=record["course_subject_prefix"],
                number=record["course_number"],
                title=record["course_title"],
                sections=[],
            )
        periods = fetch_course_section_periods(semester_id, record["crn"])

        courses[key].sections.append(CourseSection.from_record(record, periods))

    return list(courses.values())


def fetch_courses_without_sections(
    semester_id: str, limit: int, offset: int, **search
) -> Dict[str, Any]:
    c = conn.cursor()

    q: QueryBuilder = (
        course_sections_q.select(course_sections_t.semester_id)
        .select(course_sections_t.course_subject_prefix.as_("subject_prefix"))
        .select(course_sections_t.course_number.as_("number"))
        .select(course_sections_t.course_title.as_("title"))
        .where(course_sections_t.semester_id == semester_id)
        .limit(limit)
        .offset(offset)
        .groupby(course_sections_t.semester_id)
        .groupby(course_sections_t.course_subject_prefix)
        .groupby(course_sections_t.course_number)
        .groupby(course_sections_t.course_title)
    )

    print(q)
    c.execute(q.get_sql())
    return c.fetchall()


def records_to_sections(semester_id: str, records: List[Dict]) -> List[CourseSection]:
    sections = []
    for record in records:
        periods = fetch_course_section_periods(semester_id, record["crn"])

        sections.append(CourseSection.from_record(record, periods))
    return sections
