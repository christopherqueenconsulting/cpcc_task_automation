# File Upload from URLs and Google Drive

## Overview

The CPCC Task Automation system now supports flexible file uploads that allow instructors to provide files through multiple methods:
1. **Local file upload** (traditional drag-and-drop or file browser)
2. **Google Drive URLs** (share links from Google Drive)
3. **Direct download URLs** (any publicly accessible HTTP/HTTPS URL)

This feature is available on all grading pages where file uploads are required.

## How to Use

### Tab-Based Interface

When you see a file upload widget on a grading page, you'll notice two tabs:
- **📁 Upload File(s)** - Traditional local file upload
- **🔗 From URL/Google Drive** - Download files from the internet

### Uploading from Local Files

1. Click the "📁 Upload File(s)" tab
2. Use the file browser or drag-and-drop to select files from your computer
3. Files are uploaded immediately

### Downloading from Google Drive

1. Click the "🔗 From URL/Google Drive" tab
2. Paste your Google Drive share link into the "File URL" field
3. Click the "📥 Download from URL" button
4. The file will be downloaded and saved for use

**Supported Google Drive URL formats:**
- `https://drive.google.com/file/d/FILE_ID/view?usp=sharing`
- `https://drive.google.com/open?id=FILE_ID`
- `https://drive.google.com/uc?id=FILE_ID&export=download`

**To get a Google Drive share link:**
1. In Google Drive, right-click on your file
2. Select "Get link" or "Share"
3. Set permissions to "Anyone with the link"
4. Copy the link URL
5. Paste it into the file upload widget

### Downloading from Direct URLs

1. Click the "🔗 From URL/Google Drive" tab
2. Paste any direct download URL into the "File URL" field
3. Click the "📥 Download from URL" button
4. The file will be downloaded and saved for use

**Examples of direct URLs:**
- `https://example.com/documents/assignment.pdf`
- `https://mysite.edu/files/exam-solution.docx`
- `https://raw.githubusercontent.com/user/repo/main/file.txt`

## Where This Feature is Available

The flexible file upload is currently available in the following locations:

### Grade Assignment (Rubric-Based)
- **Assignment Instructions** - Upload or provide URL for exam/assignment instructions
- **Solution File** - Upload or provide URL for reference solutions (supports multiple files)
- **Student Submissions** - Upload or provide URLs for student work (supports multiple files)

## Technical Details

### File Type Detection

The system automatically detects file types through:
1. **Filename** - Extension from the URL or Google Drive filename
2. **Content-Type header** - MIME type from the HTTP response
3. **Hint fallback** - Manual extension assignment when needed

### Error Handling

The system handles various error conditions gracefully:
- **HTTP 404 (Not Found)** - Shows error message if URL doesn't exist
- **Timeouts** - Shows timeout error if download takes too long
- **Invalid URLs** - Validates URL format before attempting download
- **Permission denied** - Shows error if Google Drive file isn't shared publicly

### Security Considerations

- All downloads use secure HTTPS connections when available
- Downloaded files are stored in temporary locations
- No credentials or API keys are required for public URLs
- Google Drive files must be set to "Anyone with the link" can view

## Troubleshooting

### Google Drive File Won't Download

**Problem:** "Failed to download file" error when using Google Drive URL

**Solutions:**
1. Make sure the file is shared with "Anyone with the link" can view/download
2. Try the different URL format (use `/file/d/FILE_ID/view` format)
3. Verify the file isn't restricted by your organization's policies
4. Check if the file is too large (very large files may timeout)

### Direct URL Download Fails

**Problem:** Error downloading from a direct URL

**Solutions:**
1. Verify the URL is publicly accessible (try opening in a browser)
2. Check if the URL requires authentication or login
3. Make sure the URL points directly to the file, not a web page
4. Verify the file format is supported by the grading page

### Downloaded File Has Wrong Extension

**Problem:** File is downloaded but has `.tmp` or wrong extension

**Solutions:**
1. The system tries to detect the correct extension automatically
2. Ensure the URL or Google Drive file has a clear filename
3. The server should provide a proper `Content-Type` header
4. As a workaround, rename the file before uploading to Google Drive

## Benefits

### For Instructors
- **Flexibility** - Use files from any source (local computer, cloud storage, course management system)
- **Efficiency** - No need to download files locally first, then re-upload
- **Consistency** - Keep assignment materials in Google Drive and link directly
- **Collaboration** - Share file URLs with teaching assistants who can use them directly

### For the System
- **Reduced Storage** - Files are downloaded on-demand, not stored permanently
- **Better Caching** - Downloaded files can be reused within a session
- **Improved Workflow** - Seamless integration with existing file processing

## API Reference

For developers working on this codebase:

### `parse_google_drive_url(url: str) -> Optional[str]`
Parse Google Drive URL and extract file ID.

### `download_file_from_url(url: str, filename_hint: Optional[str] = None) -> Optional[Tuple[str, str]]`
Download file from URL or Google Drive, returns (filename, temp_path) tuple.

### `add_flexible_upload_element(..., allow_url: bool = True) -> UploadedFileResult`
Enhanced file upload widget with URL support. Set `allow_url=False` to disable URL tab.

## Future Enhancements

Potential future improvements:
- Support for Dropbox, OneDrive, and other cloud storage URLs
- Bulk URL import (paste multiple URLs at once)
- URL validation before download attempt
- Progress indicators for large file downloads
- Support for password-protected URLs (via credential management)
