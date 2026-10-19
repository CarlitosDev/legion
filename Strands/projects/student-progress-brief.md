# Student progress tracking from 1-to-1 lesson transcripts

## Objective
Analyse roughly 3,000 daily 1-to-1 lesson transcripts and track how each student develops over time.

## Context (from the project owner)
- About 3,000 transcripts per day.
- Some lessons follow a recognisable pattern (structured activities); others are free speech.
- Lessons are language-based today. Physics lessons start soon, so nothing may hard-code "language learning".

## What we need the workforce to establish (discovery questions)
- What does "development" mean per subject? (e.g. CEFR-aligned skill evidence for language; concept mastery for physics.)
- How are structured and free-speech lessons handled differently?
- What does a teacher, a student, or a learning-science lead do with the output?
- Success criteria, privacy/PII handling for student transcripts, and cost per transcript at 3,000/day.

## Constraints
- Runs on AWS eu-west-1. Cost per transcript must be explicit in the design.
- Evidence-linked outputs: every claim about a student must point back to transcript spans.
