# Attendance and EVA recovery

Attendance collects through today in the course timezone (currently America/New_York),
including submissions available when the run executes. The old two-day delay is removed.
The course end date remains the upper bound.

## Current-day attendance

Today's attendance can be saved and verified without declaring the day complete.
The completed-through checkpoint advances only through fully processed past days.
The collection date is captured before scraping, so a run crossing midnight does
not close a partially collected day. Subsequent runs revisit open days and deduplicate
student/date entries. The existing seven-day overlap also recovers late activity.

Submission-result pages are traversed automatically. If the activity inventory
itself has additional pages, the scan reports incomplete and cannot advance the
checkpoint; make the full activity list available before retrying.

Without verified history, collection starts at course start. Failed scans, unmatched
students, count mismatches, unresolved EVA reconciliation, or unverified writes leave
the run incomplete. A recovery run rechecks from course start and verifies supported
attendance again. Choose an inclusive attendance start and end date to reprocess a
bounded window. Only selected dates are written or revalidated in MyColleges; ledger
work outside the range remains pending, and the completed-through checkpoint cannot
cross those unprocessed dates. A partial range does not produce a course-wide
missing-EVA result. Full recheck uses the full course window.

The collection details list shows each ordinary or EVA activity, its selected link,
the final results page, the view that was read, page and row counts, and any error
code. Assignment results open the per-user Submissions view directly to avoid the
per-file redirect. If quiz attempt logs do not produce a readable result table, the
collector retries using the quiz Attempts page.

## Changing or re-running EVA

On Take Attendance, start the browser and wait for the course-selection form.
Enable **Manage saved EVA activities**, then choose the saved course.

- **Change assignment** clears the previous selection; choose the correct activity
  during setup when continuing the run.
- **Detach assignment** clears the activity while retaining the confirmed census date.
- **Reset EVA results** retains the activity and census date, invalidates derived EVA
  results, and requires a fresh collection.
- **Re-run EVA** prepares a fresh collection using the saved assignment and cutoff.
  Continue with the desired attendance write/dry-run options.

EVA setup and collection remain available after the deadline. Each EVA collection
reads the full course-start-to-census window, independently of the ordinary attendance
checkpoint. Ordinary activity selection still includes due dates within the attendance
window, including its boundaries. Selecting an activity for EVA does not exclude it
from ordinary attendance. Actual completion dates determine EVA qualification; activity
after the census cutoff does not qualify simply because the process ran later.

## Reconciliation and previews

Use the attendance dry-run option to preview missing entries before changing MyColleges.
A complete scan replaces the selected EVA activity's prior evidence snapshot, including
when the new snapshot has no qualifying completions. Partial scans cannot remove old
support or produce definitive missing-EVA conclusions.

Changing/resetting EVA archives the previous evidence and configuration. Independent
ordinary attendance evidence remains valid. EVA-only rows that may already have reached
MyColleges are marked **review_required** and shown in the management panel. The process
does not silently delete recorded attendance.

Review those records against MyColleges and correct unsupported attendance there. The
next run checks the affected dates: a definite read-back of absence resolves the review;
unreadable dates remain unresolved. Fresh independent qualifying evidence can also
reactivate a row and verify it normally. No manual database deletion is required.

Legacy activity identifiers are migrated to identities including the LMS course and
activity type. Ambiguous legacy evidence requires review rather than guessing its source.

## Understanding EVA results

The result distinguishes setup required, incomplete collection, pending attendance, and
verified results. Verified means the collection and attendance checks succeeded; the
student list then identifies students without qualifying attendance through census.

The process checks dated verified attendance through the EVA cutoff. Cumulative totals
alone cannot prove when a student attended. Unexplained manual attendance, missing roster
totals, failed historical read-back, or count discrepancies produce an incomplete result
requiring review. They do not generate definitive missing-EVA advice.

Every check has a persisted header, including empty and incomplete results, so successful
recovery clears stale student warnings. Reset history and previous runs remain available
in the local SQLite ledger. Keep the configured ledger on durable storage.
