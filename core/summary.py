from dataclasses import dataclass, field, asdict
from collections import Counter

# Skip reasons (pre-download filtering, see Creator.download())
SKIP_FILTERED = 'filtered_type_or_extension'
SKIP_REGEX = 'filtered_regex'
SKIP_EXISTING = 'already_downloaded'

# Failure reasons (download/extraction attempts)
FAIL_NOT_FOUND = 'not_found_404'
FAIL_TIMEOUT = 'timeout'
FAIL_PASSWORD_MISSING = 'archive_password_missing'
FAIL_PASSWORD_INCORRECT = 'archive_password_incorrect'
FAIL_EXTRACTION_ERROR = 'extraction_error'
FAIL_EXTERNAL_UNSUPPORTED = 'external_link_unsupported'
FAIL_EXTERNAL_QUOTA = 'external_quota_exceeded'
FAIL_EXTERNAL_DOWNLOAD = 'external_download_error'

class DownloadError(Exception):
    """Base for classified download/extraction failures - never raised directly. `reason` is one
    of the FAIL_* constants above, set by each subclass."""
    reason: str

class NotFoundError(DownloadError):
    reason = FAIL_NOT_FOUND

class DownloadTimeoutError(DownloadError):
    reason = FAIL_TIMEOUT

class ArchivePasswordMissingError(DownloadError):
    reason = FAIL_PASSWORD_MISSING

class ArchivePasswordIncorrectError(DownloadError):
    reason = FAIL_PASSWORD_INCORRECT

class ExtractionError(DownloadError):
    reason = FAIL_EXTRACTION_ERROR

class UnsupportedLinkError(DownloadError):
    """A Drive/Mega link type this project can't handle, e.g. Mega's `#P!...` link passwords."""
    reason = FAIL_EXTERNAL_UNSUPPORTED

class QuotaExceededError(DownloadError):
    """Not retried within the run - Drive/Mega quotas reset on their own schedule."""
    reason = FAIL_EXTERNAL_QUOTA

class ExternalDownloadError(DownloadError):
    reason = FAIL_EXTERNAL_DOWNLOAD

@dataclass
class FileOutcome:
    status: str  # 'success' | 'failed'
    reason: str | None = None

@dataclass
class CreatorSummary:
    service: str
    id: str
    name: str = ''
    status: str = 'completed'  # 'completed' | 'no_new_files' | 'failed'
    error: str | None = None
    files_downloaded: int = 0
    files_skipped: dict[str, int] = field(default_factory=dict)
    files_failed: dict[str, int] = field(default_factory=dict)
    posts_not_imported: int = 0

    def total_skipped(self) -> int:
        return sum(self.files_skipped.values())

    def total_failed(self) -> int:
        return sum(self.files_failed.values())

@dataclass
class RunSummary:
    creators: list[CreatorSummary] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'creators': [asdict(c) for c in self.creators]}

    def render_text(self) -> str:
        """Formats the run into three sections: Overview (totals only), Updates (one line per
        creator that had files processed), and Failures (one line per creator that failed
        entirely before any file was attempted)."""

        completed = [c for c in self.creators if c.status == 'completed']
        no_new_files = [c for c in self.creators if c.status == 'no_new_files']
        failed = [c for c in self.creators if c.status == 'failed']

        total_downloaded = sum(c.files_downloaded for c in completed)
        total_skipped = Counter()
        total_failed = Counter()
        for c in completed:
            total_skipped.update(c.files_skipped)
            total_failed.update(c.files_failed)

        def reasons_str(counts) -> str:
            return ', '.join(f'{reason} {count}' for reason, count in counts.items())

        def inline_reasons(counts: dict) -> str:
            if not counts:
                return ''
            return ' (' + ', '.join(f'{reason}: {count}' for reason, count in counts.items()) + ')'

        lines = []
        lines.append('=' * 22 + ' RUN SUMMARY ' + '=' * 22)
        lines.append('Overview:')
        lines.append(
            f'  Creators: {len(self.creators)} processed - {len(completed)} completed, '
            f'{len(no_new_files)} no new files, {len(failed)} failed'
        )
        lines.append(
            f'  Files:    {total_downloaded} downloaded, {sum(total_skipped.values())} skipped, '
            f'{sum(total_failed.values())} failed'
        )
        if total_skipped:
            lines.append(f'    skipped: {reasons_str(total_skipped)}')
        if total_failed:
            lines.append(f'    failed:  {reasons_str(total_failed)}')

        lines.append('')
        lines.append('Updates:')
        if completed:
            for c in completed:
                lines.append(
                    f'  {c.name} ({c.service}/{c.id}): {c.files_downloaded} downloaded, '
                    f'{c.total_skipped()} skipped{inline_reasons(c.files_skipped)}, '
                    f'{c.total_failed()} failed{inline_reasons(c.files_failed)}'
                )
        else:
            lines.append('  (none)')

        lines.append('')
        lines.append('Failures:')
        if failed:
            for c in failed:
                lines.append(f'  {c.service}/{c.id}: {c.error}')
        else:
            lines.append('  (none)')

        lines.append('=' * 59)
        return '\n'.join(lines)
