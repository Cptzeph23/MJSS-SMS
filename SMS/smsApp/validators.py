# Absolute path: SMS/smsApp/validators.py
"""
Upload validators (spec §27: 'File validation', 'Upload size restrictions').

Two layers, both needed:
1. Size limit — cheap, prevents a single upload from exhausting storage
   or memory during processing.
2. Content-type sniffing via python-magic (libmagic) — reads the file's
   actual magic bytes rather than trusting its extension or the
   browser-supplied Content-Type header, both of which are trivially
   spoofable. A renamed `malware.exe` -> `photo.jpg` fails this check
   even though its filename and claimed MIME type look fine.

DEPLOYMENT NOTE: python-magic requires the `libmagic` C library on the
host. On Debian/Ubuntu (Render's build images), install it with:
    apt-get install -y libmagic1
This is a system package, not something `pip install` provides — same
category of requirement as WeasyPrint's Cairo/Pango dependency (Phase 9).
"""
from __future__ import annotations

import os
import uuid

import magic
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.template.defaultfilters import filesizeformat
from django.utils.deconstruct import deconstructible
from django.core.exceptions import ValidationError

@deconstructible
class MaxFileSizeValidator:
    """Class-based (not closure-based) so Django's migration writer can
    serialize it — a plain nested function like `validate_file_size(5)`
    returning a closure cannot be referenced by dotted path in a
    generated migration file. `@deconstructible` gives this class a
    `deconstruct()` Django's serializer knows how to call."""

    def __init__(self, max_mb: int):
        self.max_mb = max_mb

    def __call__(self, file_obj):
        max_bytes = self.max_mb * 1024 * 1024
        if file_obj.size > max_bytes:
            raise ValidationError(
                f"File too large ({filesizeformat(file_obj.size)}). "
                f"Maximum allowed size is {self.max_mb} MB."
            )

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_mb == other.max_mb


def validate_file_size(max_mb: int):
    """Returns a validator enforcing a max upload size, in megabytes.
    Used as e.g. `validators=[validate_file_size(10)]` on a FileField."""
    return MaxFileSizeValidator(max_mb)


# MIME types accepted per document category. Kept as named sets rather
# than one giant list so each field can opt into exactly what it needs
# (a profile photo field shouldn't silently start accepting PDFs).
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DOCUMENT_MIME_TYPES = IMAGE_MIME_TYPES | {"application/pdf"}
PDF_MIME_TYPES = {"application/pdf"}
# Broader set for LMS course materials/resources (spec §10: PDF, Documents,
# Images, Videos, Presentations) — video and presentation formats aren't
# images or PDFs, so they need their own allowed set.
COURSE_MATERIAL_MIME_TYPES = DOCUMENT_MIME_TYPES | {
    "video/mp4", "video/webm", "video/quicktime",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.ms-powerpoint",  # legacy .ppt
    "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _validate_content_type(file_obj, allowed_mime_types: set[str], label: str):
    file_obj.seek(0)
    header = file_obj.read(2048)
    file_obj.seek(0)

    detected_type = magic.from_buffer(header, mime=True)
    if detected_type not in allowed_mime_types:
        raise ValidationError(
            f"Unsupported file type ({detected_type}). "
            f"Only {label} are allowed, regardless of file extension."
        )


def validate_image_content(file_obj):
    """Real content-sniffing for image uploads (photos, logos, cover
    images) — spec §27 'File validation'."""
    _validate_content_type(file_obj, IMAGE_MIME_TYPES, "images (JPEG, PNG, WEBP, GIF)")


def validate_document_content(file_obj):
    """For general document uploads (staff/student documents, course
    materials, assignment submissions) — images or PDFs."""
    _validate_content_type(file_obj, DOCUMENT_MIME_TYPES, "images or PDFs")


def validate_pdf_content(file_obj):
    """For fields that should only ever contain system-generated PDFs
    (report cards, transcripts, receipts)."""
    _validate_content_type(file_obj, PDF_MIME_TYPES, "PDF files")


def validate_course_material_content(file_obj):
    """Spec §10 course content types: PDF, Documents, Images, Videos,
    Presentations. Note this only checks the container format, not video/
    presentation content itself (e.g. it can't detect a malicious script
    embedded in a crafted MP4) — pair with a virus-scanning step at the
    storage layer for stronger guarantees in a high-stakes deployment."""
    _validate_content_type(
        file_obj, COURSE_MATERIAL_MIME_TYPES,
        "PDFs, images, videos, presentations, or documents",
    )


def validate_upload(
    uploaded_file: UploadedFile | None,
    content_validator,
    size_limit_mb: int,
) -> UploadedFile | None:
    """Validate and rename an uploaded file.

    Performs both size and content validation, then renames the file to
    remove the original filename (security: prevents information leaks
    and executable detection by name extension). Returns the file object
    with a renamed name attribute.

    Raises ValidationError if validation fails. Returns None if the input
    file is None (supporting optional file uploads).

    Args:
        uploaded_file: The file from request.FILES, or None
        content_validator: Function like validate_document_content that
                          checks file content (magic bytes)
        size_limit_mb: Max file size in MB (passed to MaxFileSizeValidator)

    Returns:
        The same UploadedFile object, with name attribute changed to
        <uuid>.<extension> to strip the original filename.

    Raises:
        ValidationError: If file size or content validation fails.
    """
    if uploaded_file is None:
        return None

    # Validate file size
    size_validator = MaxFileSizeValidator(size_limit_mb)
    size_validator(uploaded_file)

    # Validate file content
    content_validator(uploaded_file)

    # Rename: extract extension and generate a new name without the original filename
    # This prevents info leaks and avoids relying on the filename for type detection
    _, ext = os.path.splitext(uploaded_file.name)
    # Generate a random component; Django's default upload_to will prepend the path
    new_name = f"{uuid.uuid4().hex}{ext}"
    uploaded_file.name = new_name

    return uploaded_file

# Define the custom exception expected by views.py
class FileValidationError(ValidationError):
    """Exception raised when file validation fails."""
    pass

# Map MIME types / categories for LMS materials
COURSE_MATERIAL_TYPE_TO_CATEGORY = {
    # Values must match CourseMaterial.MaterialType, whose stored choice
    # values are uppercase. The previous lowercase keys caused every file
    # upload from the dashboard to be rejected before validation/storage.
    "PDF": PDF_MIME_TYPES,
    "IMAGE": IMAGE_MIME_TYPES,
    "DOCUMENT": DOCUMENT_MIME_TYPES,
    "VIDEO": COURSE_MATERIAL_MIME_TYPES,
    "PRESENTATION": COURSE_MATERIAL_MIME_TYPES,
}
