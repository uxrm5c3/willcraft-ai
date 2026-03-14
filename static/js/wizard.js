/* WillCraft AI - Wizard JavaScript (v20260315a) */

// Apply dropdown filtering on initial page load
document.addEventListener('DOMContentLoaded', function() {
    // Filter person dropdowns based on data-role attributes
    if (document.querySelector('select.person-select[data-role]')) {
        refreshPersonDropdowns();
    }
});

// Save Draft via AJAX
async function saveDraft() {
    const statusEl = document.getElementById('save-status');
    const statusMobile = document.getElementById('save-status-mobile');
    if (statusEl) statusEl.textContent = 'Saving...';
    if (statusMobile) statusMobile.textContent = 'Saving...';
    try {
        const resp = await fetch('/api/will/save', { method: 'POST' });
        const data = await resp.json();
        const msg = data.ok ? 'Saved!' : 'Error saving.';
        if (statusEl) { statusEl.textContent = msg; setTimeout(() => { statusEl.textContent = ''; }, 3000); }
        if (statusMobile) { statusMobile.textContent = msg; setTimeout(() => { statusMobile.textContent = ''; }, 3000); }
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Save failed.';
        if (statusMobile) statusMobile.textContent = 'Save failed.';
    }
}

// Helper to set form field value
function setFieldValue(fieldName, value) {
    const field = document.querySelector(`[name="${fieldName}"]`);
    if (field && value) {
        field.value = value;
        field.classList.add('bg-yellow-50');
    }
}

function setValueIfEmpty(fieldName, value) {
    const field = document.querySelector(`[name="${fieldName}"]`);
    if (field && value) {
        if (!field.value || field.value.trim() === '') {
            field.value = value;
        }
        field.classList.add('bg-yellow-50');
    }
}

function convertDateForInput(dateStr) {
    if (!dateStr) return '';
    const parts = dateStr.split('-');
    if (parts.length === 3 && parts[0].length <= 2) {
        return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
    }
    return dateStr;
}

// File Upload Handlers
async function uploadFile(file, uploadId, category) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('category', category);
    const statusEl = document.getElementById(`upload-status-${uploadId}`);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">Uploading...</span>';
    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok) {
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">Uploaded successfully!</span>';
            return data;
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed.</span>';
    }
    return null;
}

function handleFileSelect(input, uploadId, category) {
    for (const file of input.files) {
        uploadFile(file, uploadId, category);
    }
}

function handleDrop(event, uploadId, category) {
    event.preventDefault();
    event.currentTarget.classList.remove('border-primary-500', 'bg-primary-50');
    for (const file of event.dataTransfer.files) {
        uploadFile(file, uploadId, category);
    }
}

// ===========================================================================
// CLIENT-SIDE FILE VALIDATION
// ===========================================================================
const MAX_FILE_SIZE_MB = 10;
const ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/heic', 'image/heif', 'image/bmp', 'image/tiff'];
const ALLOWED_DOC_TYPES = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];

function validateFile(file) {
    if (!file) return { valid: false, error: 'No file selected.' };
    const sizeMB = file.size / (1024 * 1024);
    if (sizeMB > MAX_FILE_SIZE_MB) {
        return { valid: false, error: `File too large (${sizeMB.toFixed(1)}MB). Maximum is ${MAX_FILE_SIZE_MB}MB.` };
    }
    if (file.type && !ALLOWED_IMAGE_TYPES.includes(file.type) && !ALLOWED_DOC_TYPES.includes(file.type)) {
        const ext = (file.name || '').split('.').pop().toLowerCase();
        const allowedExts = ['png','jpg','jpeg','gif','pdf','docx','doc','heic','heif','webp','bmp','tiff','tif'];
        if (ext && !allowedExts.includes(ext)) {
            return { valid: false, error: `File type not supported: .${ext}` };
        }
    }
    return { valid: true };
}

// ===========================================================================
// MOBILE DETECTION
// ===========================================================================
function isMobileDevice() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
        || (navigator.maxTouchPoints && navigator.maxTouchPoints > 2);
}

// ===========================================================================
// DOCUMENT PREVIEW & VIEWER
// ===========================================================================

/**
 * Show the document preview section in the identity modal.
 * @param {string} documentId - Document ID from API
 * @param {string} filename - Original filename
 * @param {File|null} localFile - Optional local file for immediate preview
 */
function showDocumentPreview(documentId, filename, localFile) {
    const docIdField = document.getElementById('modal-document-id');
    if (docIdField) docIdField.value = documentId || '';

    const container = document.getElementById('modal-document-preview');
    if (!container) return;

    const imgEl = document.getElementById('doc-preview-img');
    const pdfEl = document.getElementById('doc-preview-pdf');
    const nameEl = document.getElementById('doc-preview-name');

    if (nameEl) nameEl.textContent = filename || 'Document';

    const ext = (filename || '').split('.').pop().toLowerCase();
    const isPdf = ext === 'pdf';

    if (isPdf) {
        if (imgEl) imgEl.classList.add('hidden');
        if (pdfEl) pdfEl.classList.remove('hidden');
    } else {
        if (pdfEl) pdfEl.classList.add('hidden');
        if (imgEl) {
            imgEl.classList.remove('hidden');
            if (localFile && localFile instanceof File) {
                // Use local file for immediate preview (before save)
                const reader = new FileReader();
                reader.onload = (e) => { imgEl.src = e.target.result; };
                reader.readAsDataURL(localFile);
            } else if (documentId) {
                imgEl.src = `/api/documents/${documentId}`;
            }
        }
    }

    container.classList.remove('hidden');

    // Hide the upload buttons since doc is already uploaded
    const uploadSection = container.previousElementSibling;
    // We don't hide upload section — user can still see the retake button in preview
}

/**
 * Hide the document preview section.
 */
function hideDocumentPreview() {
    const container = document.getElementById('modal-document-preview');
    if (container) container.classList.add('hidden');
    const docIdField = document.getElementById('modal-document-id');
    if (docIdField) docIdField.value = '';
    const imgEl = document.getElementById('doc-preview-img');
    if (imgEl) imgEl.src = '';
}

/**
 * Open the full-screen document viewer lightbox.
 */
function viewDocument() {
    const docId = document.getElementById('modal-document-id')?.value;
    if (!docId) return;

    const viewer = document.getElementById('document-viewer');
    if (!viewer) return;

    const imgEl = document.getElementById('doc-viewer-img');
    const pdfEl = document.getElementById('doc-viewer-pdf');
    const titleEl = document.getElementById('doc-viewer-title');

    const nameEl = document.getElementById('doc-preview-name');
    const filename = nameEl ? nameEl.textContent : 'Document';
    if (titleEl) titleEl.textContent = filename;

    const ext = filename.split('.').pop().toLowerCase();
    const isPdf = ext === 'pdf';

    if (isPdf) {
        if (imgEl) imgEl.classList.add('hidden');
        if (pdfEl) {
            pdfEl.classList.remove('hidden');
            pdfEl.src = `/api/documents/${docId}`;
        }
    } else {
        if (pdfEl) pdfEl.classList.add('hidden');
        if (imgEl) {
            imgEl.classList.remove('hidden');
            imgEl.src = `/api/documents/${docId}`;
        }
    }

    viewer.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

/**
 * View a document by ID (callable from anywhere, e.g., identity cards).
 */
function viewDocumentById(documentId, filename) {
    if (!documentId) return;

    const viewer = document.getElementById('document-viewer');
    if (!viewer) return;

    const imgEl = document.getElementById('doc-viewer-img');
    const pdfEl = document.getElementById('doc-viewer-pdf');
    const titleEl = document.getElementById('doc-viewer-title');

    if (titleEl) titleEl.textContent = filename || 'Document';

    const ext = (filename || '').split('.').pop().toLowerCase();
    const isPdf = ext === 'pdf';

    if (isPdf) {
        if (imgEl) imgEl.classList.add('hidden');
        if (pdfEl) { pdfEl.classList.remove('hidden'); pdfEl.src = `/api/documents/${documentId}`; }
    } else {
        if (pdfEl) pdfEl.classList.add('hidden');
        if (imgEl) { imgEl.classList.remove('hidden'); imgEl.src = `/api/documents/${documentId}`; }
    }

    viewer.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

/**
 * Close the document viewer lightbox.
 */
function closeDocumentViewer() {
    const viewer = document.getElementById('document-viewer');
    if (viewer) viewer.classList.add('hidden');
    document.body.style.overflow = '';
    // Clear sources to stop any loading
    const imgEl = document.getElementById('doc-viewer-img');
    const pdfEl = document.getElementById('doc-viewer-pdf');
    if (imgEl) imgEl.src = '';
    if (pdfEl) { pdfEl.src = ''; pdfEl.classList.add('hidden'); }
}

/**
 * Retake/replace the uploaded document.
 */
function retakeDocument() {
    openCameraForNRIC();
}

/**
 * Remove the uploaded document from the identity modal.
 */
async function removeDocument() {
    if (!confirm('Are you sure you want to remove this document?')) return;
    const docId = document.getElementById('modal-document-id')?.value;
    if (docId) {
        try {
            await fetch(`/api/documents/${docId}`, { method: 'DELETE' });
        } catch (e) {
            console.error('Failed to delete document:', e);
        }
    }
    hideDocumentPreview();
}

// Keyboard shortcut: Escape to close document viewer
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const viewer = document.getElementById('document-viewer');
        if (viewer && !viewer.classList.contains('hidden')) {
            closeDocumentViewer();
            e.stopPropagation();
        }
    }
});

// ===========================================================================
// CAMERA VIEWFINDER WITH IC/DOCUMENT GUIDE BOX
// ===========================================================================

let _cameraStream = null;
let _cameraFacingMode = 'environment';
let _cameraCapturedBlob = null;
let _cameraCallback = null;
let _cameraDocType = 'nric';

/**
 * Open camera viewfinder with document guide overlay.
 * On mobile: uses native camera via file input (more reliable).
 * On desktop: tries getUserMedia with in-browser viewfinder.
 * @param {Function} callback - Called with captured File object
 * @param {string} docType - 'nric', 'property', 'financial'
 */
function openCameraViewfinder(callback, docType) {
    _cameraCallback = callback;
    _cameraDocType = docType || 'nric';
    _cameraCapturedBlob = null;

    // On mobile, go straight to native camera (more reliable than getUserMedia)
    if (isMobileDevice()) {
        _openNativeCamera(callback, docType);
        return;
    }

    // Desktop: try in-browser viewfinder with getUserMedia
    _openDesktopViewfinder(callback, docType);
}

/**
 * Mobile: Open native camera via file input with capture attribute.
 * This is the most reliable way to access camera on iOS/Android.
 */
function _openNativeCamera(callback, docType) {
    const tmp = document.createElement('input');
    tmp.type = 'file';
    tmp.accept = 'image/*';
    tmp.capture = 'environment';
    tmp.style.display = 'none';
    document.body.appendChild(tmp);
    tmp.onchange = function() {
        if (tmp.files && tmp.files[0]) {
            callback(tmp.files[0]);
        }
        document.body.removeChild(tmp);
    };
    // Cleanup if cancelled (no change event fired for cancel on most browsers)
    tmp.addEventListener('cancel', function() {
        document.body.removeChild(tmp);
    });
    tmp.click();
}

/**
 * Desktop: Use getUserMedia with in-browser viewfinder overlay.
 */
async function _openDesktopViewfinder(callback, docType) {
    const titleEl = document.getElementById('camera-title');
    const subtitleEl = document.getElementById('camera-subtitle');
    const guideTextEl = document.getElementById('camera-guide-text');
    const cornersEl = document.getElementById('camera-corners');
    const maskCutout = document.getElementById('mask-cutout');
    const guideBorder = document.getElementById('guide-border');

    // Configure guide box shape based on document type
    if (docType === 'nric') {
        if (titleEl) titleEl.textContent = 'Scan IC / Passport';
        if (subtitleEl) subtitleEl.textContent = 'Fit your IC card inside the frame';
        if (guideTextEl) guideTextEl.textContent = 'Place IC / Passport here';
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','25%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','50%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','25%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','50%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:25%;width:90%;height:50%;position:absolute;'; }
    } else if (docType === 'property' || docType === 'financial') {
        if (titleEl) titleEl.textContent = docType === 'property' ? 'Scan Property Document' : 'Scan Financial Document';
        if (subtitleEl) subtitleEl.textContent = 'Fit the document inside the frame';
        if (guideTextEl) guideTextEl.textContent = 'Place document here';
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','10%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','75%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','10%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','75%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:10%;width:90%;height:75%;position:absolute;'; }
    } else {
        if (titleEl) titleEl.textContent = 'Scan Document';
        if (subtitleEl) subtitleEl.textContent = 'Fit the document inside the frame';
        if (guideTextEl) guideTextEl.textContent = 'Align document here';
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','20%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','55%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','20%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','55%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:20%;width:90%;height:55%;position:absolute;'; }
    }

    // Show modal, reset state
    const modal = document.getElementById('camera-modal');
    if (modal) {
        modal.classList.remove('hidden');
        document.getElementById('camera-controls').classList.remove('hidden');
        document.getElementById('camera-preview-controls').classList.add('hidden');
        document.getElementById('camera-video').classList.remove('hidden');
        document.getElementById('camera-preview').classList.add('hidden');
        document.getElementById('camera-overlay').classList.remove('hidden');
        document.getElementById('camera-processing').classList.add('hidden');
    }

    await startCamera();
}

async function startCamera() {
    stopCameraStream();
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('getUserMedia not supported');
        }
        _cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: _cameraFacingMode, width: { ideal: 1920 }, height: { ideal: 1080 } }
        });
        const video = document.getElementById('camera-video');
        video.srcObject = _cameraStream;
        await video.play();
    } catch (err) {
        console.warn('Camera error (falling back to file picker):', err.message || err);
        closeCameraModal();
        // Fallback: open file picker
        _openNativeCamera(_cameraCallback || function(){}, _cameraDocType);
    }
}

function stopCameraStream() {
    if (_cameraStream) {
        _cameraStream.getTracks().forEach(t => t.stop());
        _cameraStream = null;
    }
}

async function switchCamera() {
    _cameraFacingMode = _cameraFacingMode === 'environment' ? 'user' : 'environment';
    await startCamera();
}

function capturePhoto() {
    const video = document.getElementById('camera-video');
    const canvas = document.getElementById('camera-canvas');
    const preview = document.getElementById('camera-preview');

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    canvas.toBlob((blob) => {
        _cameraCapturedBlob = blob;
        const url = URL.createObjectURL(blob);
        preview.src = url;
        preview.classList.remove('hidden');
        video.classList.add('hidden');
        document.getElementById('camera-overlay').classList.add('hidden');
        document.getElementById('camera-controls').classList.add('hidden');
        document.getElementById('camera-preview-controls').classList.remove('hidden');
        stopCameraStream();
    }, 'image/jpeg', 0.92);
}

function retakePhoto() {
    _cameraCapturedBlob = null;
    document.getElementById('camera-preview').classList.add('hidden');
    document.getElementById('camera-video').classList.remove('hidden');
    document.getElementById('camera-overlay').classList.remove('hidden');
    document.getElementById('camera-controls').classList.remove('hidden');
    document.getElementById('camera-preview-controls').classList.add('hidden');
    startCamera();
}

function usePhoto() {
    if (!_cameraCapturedBlob || !_cameraCallback) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const file = new File([_cameraCapturedBlob], `${_cameraDocType}_${timestamp}.jpg`, { type: 'image/jpeg' });
    closeCameraModal();
    _cameraCallback(file);
}

function handleGallerySelect(input) {
    if (input.files[0] && _cameraCallback) {
        closeCameraModal();
        _cameraCallback(input.files[0]);
    }
}

function closeCameraModal() {
    stopCameraStream();
    const modal = document.getElementById('camera-modal');
    if (modal) modal.classList.add('hidden');
    const proc = document.getElementById('camera-processing');
    if (proc) proc.classList.add('hidden');
    _cameraCapturedBlob = null;
    const gi = document.getElementById('camera-gallery-input');
    if (gi) gi.value = '';
}


// ===========================================================================
// OCR CONFIRMATION MODAL (for file uploads - shows review before applying)
// ===========================================================================

let _ocrPendingData = null;
let _ocrPendingCallback = null;
let _ocrRetryInputEl = null;
let _ocrRetryCamera = null;

const OCR_FIELD_LABELS = {
    full_name: 'Full Name', nric_number: 'NRIC / Passport No.', date_of_birth: 'Date of Birth',
    address: 'Address', gender: 'Gender', nationality: 'Nationality', passport_expiry: 'Passport Expiry',
    property_address: 'Property Address', title_type: 'Title Type', lot_number: 'Lot Number',
    title_number: 'Title / Hakmilik No.', bandar_pekan: 'Bandar / Pekan', mukim: 'Mukim',
    daerah: 'Daerah (District)', negeri: 'Negeri (State)', property_description: 'Description',
    institution: 'Institution', account_number: 'Account Number', type: 'Asset Type',
    description: 'Description', account_holder_name: 'Account Holder', account_holder_nric: 'Account Holder NRIC',
};

function formatFieldLabel(key) {
    return OCR_FIELD_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function showOCRConfirmation(extracted, imageFile, callback, retryInputEl, retryArgs, retryCameraInfo) {
    _ocrPendingData = extracted;
    _ocrPendingCallback = callback;
    _ocrRetryInputEl = retryInputEl;
    _ocrRetryCamera = retryCameraInfo || null;

    const previewImg = document.getElementById('ocr-preview-img');
    if (imageFile && imageFile.type && imageFile.type.startsWith('image/')) {
        const reader = new FileReader();
        reader.onload = (e) => { previewImg.src = e.target.result; };
        reader.readAsDataURL(imageFile);
        document.getElementById('ocr-image-section').classList.remove('hidden');
    } else {
        previewImg.src = '';
        document.getElementById('ocr-image-section').classList.add('hidden');
    }

    const hasError = extracted.error || Object.values(extracted).every(v => !v || v === '');
    const warningEl = document.getElementById('ocr-warning');
    if (hasError) {
        warningEl.classList.remove('hidden');
        warningEl.innerHTML = '<p class="text-sm text-amber-700"><strong>⚠ Image may be unclear.</strong> Please verify or upload a clearer image.</p>';
    } else {
        const filled = Object.entries(extracted).filter(([k, v]) => v && v !== '' && k !== 'error' && k !== 'raw').length;
        const total = Object.keys(extracted).filter(k => k !== 'error' && k !== 'raw').length;
        if (filled < total * 0.5) {
            warningEl.classList.remove('hidden');
            warningEl.innerHTML = '<p class="text-sm text-amber-700"><strong>⚠ Low confidence.</strong> Please review or upload a better image.</p>';
        } else {
            warningEl.classList.add('hidden');
        }
    }

    const container = document.getElementById('ocr-fields-container');
    container.innerHTML = '';
    for (const [key, value] of Object.entries(extracted)) {
        if (key === 'error' || key === 'raw' || key === 'assets') continue;
        const label = formatFieldLabel(key);
        const isEmpty = !value || value.toString().trim() === '';
        const bc = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
        const isAddress = key.toLowerCase().includes('address');
        const displayValue = (value||'').toString().replace(/"/g,'&quot;');
        if (isAddress) {
            // Address comes as multi-line from server (line1\nline2\nline3)
            // Real newlines display correctly in textarea
            let textareaValue = (value||'').toString();
            container.innerHTML += `<div class="flex flex-col gap-1">
                <label class="text-sm font-medium text-gray-700">${label}</label>
                <textarea name="ocr-field-${key}" rows="4"
                       class="w-full px-3 py-1.5 border ${bc} rounded-lg text-sm focus:ring-2 focus:ring-primary-500">${textareaValue}</textarea>
            </div>`;
        } else {
            container.innerHTML += `<div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                <label class="text-sm font-medium text-gray-700 sm:w-40 shrink-0">${label}</label>
                <input name="ocr-field-${key}" value="${displayValue}"
                       class="flex-1 px-3 py-1.5 border ${bc} rounded-lg text-sm focus:ring-2 focus:ring-primary-500">
            </div>`;
        }
    }
    if (extracted.assets && extracted.assets.length > 0) {
        for (const [key, value] of Object.entries(extracted.assets[0])) {
            const label = formatFieldLabel(key);
            const isEmpty = !value || value.toString().trim() === '';
            const bc = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
            container.innerHTML += `<div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                <label class="text-sm font-medium text-gray-700 sm:w-40 shrink-0">${label}</label>
                <input name="ocr-field-asset_${key}" value="${(value||'').toString().replace(/"/g,'&quot;')}"
                       class="flex-1 px-3 py-1.5 border ${bc} rounded-lg text-sm focus:ring-2 focus:ring-primary-500">
            </div>`;
        }
    }
    document.getElementById('ocr-confirm-modal').classList.remove('hidden');
}

function applyOCRData() {
    const fields = document.querySelectorAll('#ocr-fields-container input, #ocr-fields-container textarea');
    const data = {};
    const assetData = {};
    fields.forEach(f => {
        if (f.name.startsWith('ocr-field-asset_')) {
            assetData[f.name.replace('ocr-field-asset_', '')] = f.value;
        } else if (f.name.startsWith('ocr-field-')) {
            data[f.name.replace('ocr-field-', '')] = f.value;
        }
    });
    if (Object.keys(assetData).length > 0) data.assets = [assetData];
    if (_ocrPendingCallback) _ocrPendingCallback(data);
    closeOCRModal();
}

function retryOCRUpload() {
    closeOCRModal();
    if (_ocrRetryCamera) {
        openCameraViewfinder(_ocrRetryCamera.callback, _ocrRetryCamera.docType);
        return;
    }
    if (_ocrRetryInputEl) { _ocrRetryInputEl.value = ''; _ocrRetryInputEl.click(); }
}

function closeOCRModal() {
    document.getElementById('ocr-confirm-modal').classList.add('hidden');
    _ocrPendingData = null;
    _ocrPendingCallback = null;
    // Clear file inputs so the same file can be re-selected
    const modalUpload = document.getElementById('modal-nric-upload');
    if (modalUpload) modalUpload.value = '';
    // Clear status messages
    const modalStatus = document.getElementById('modal-nric-status');
    if (modalStatus) modalStatus.innerHTML = '';
}


// ===========================================================================
// NRIC / Passport OCR Upload — auto-fills form after scan
// ===========================================================================

async function uploadAndExtractNRIC(inputOrFile, statusElId, fieldMapping) {
    let file = (inputOrFile instanceof File) ? inputOrFile : inputOrFile.files[0];
    if (!file) return;

    const validation = validateFile(file);
    const statusEl = document.getElementById(statusElId);
    if (!validation.valid) {
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/nric', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = 'Could not scan the document. Please try again with a clearer image.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            showOCRConfirmation(data.extracted, file, (confirmed) => {
                if (fieldMapping.name && confirmed.full_name) setValueIfEmpty(fieldMapping.name, confirmed.full_name);
                if (fieldMapping.nric && confirmed.nric_number) setValueIfEmpty(fieldMapping.nric, confirmed.nric_number);
                if (fieldMapping.address && confirmed.address) setValueIfEmpty(fieldMapping.address, confirmed.address);
                if (fieldMapping.dob && confirmed.date_of_birth) {
                    const d = convertDateForInput(confirmed.date_of_birth);
                    if (d) setValueIfEmpty(fieldMapping.dob, d);
                }
                if (fieldMapping.gender && confirmed.gender) {
                    const g = document.querySelector(`[name="${fieldMapping.gender}"]`);
                    if (g) { g.value = confirmed.gender; g.classList.add('bg-yellow-50'); }
                }
                if (fieldMapping.nationality && confirmed.nationality) setValueIfEmpty(fieldMapping.nationality, confirmed.nationality);
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractNRIC(f, statusElId, fieldMapping), docType: 'nric' });
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the document. Please try a clearer image.</span>';
        }
    } catch (e) {
        console.error('NRIC upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}


// ===========================================================================
// Property Document OCR Upload
// ===========================================================================

async function uploadAndExtractProperty(inputOrFile, statusElId, giftIndex, docType) {
    let file = (inputOrFile instanceof File) ? inputOrFile : inputOrFile.files[0];
    if (!file) return;

    const validation = validateFile(file);
    const statusEl = document.getElementById(statusElId);
    if (!validation.valid) {
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning property document...</span>';

    const formData = new FormData();
    formData.append('file', file);
    if (docType) formData.append('doc_type', docType);

    try {
        const resp = await fetch('/api/ocr/property', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = 'Could not read the property document. Please try again with a clearer image.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            showOCRConfirmation(data.extracted, file, (confirmed) => {
                if (confirmed.property_address) setFieldValue(`gift_prop_address_${giftIndex}`, confirmed.property_address);
                if (confirmed.title_type) {
                    const tt = document.querySelector(`[name="gift_prop_title_type_${giftIndex}"]`);
                    if (tt) { tt.value = confirmed.title_type; tt.classList.add('bg-yellow-50'); }
                }
                if (confirmed.title_number) setFieldValue(`gift_prop_title_number_${giftIndex}`, confirmed.title_number);
                if (confirmed.lot_number) setFieldValue(`gift_prop_lot_number_${giftIndex}`, confirmed.lot_number);
                if (confirmed.bandar_pekan || confirmed.mukim) setFieldValue(`gift_prop_bandar_${giftIndex}`, confirmed.bandar_pekan || confirmed.mukim || '');
                if (confirmed.daerah) setFieldValue(`gift_prop_daerah_${giftIndex}`, confirmed.daerah);
                if (confirmed.negeri) {
                    const ng = document.querySelector(`[name="gift_prop_negeri_${giftIndex}"]`);
                    if (ng) { ng.value = confirmed.negeri.toUpperCase(); ng.classList.add('bg-yellow-50'); }
                }
                const pr = document.querySelector(`[name="gift_type_${giftIndex}"][value="property"]`);
                if (pr) { pr.checked = true; switchGiftType(giftIndex, 'property'); }
                if (typeof updatePropertyPreview === 'function') updatePropertyPreview(giftIndex);
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Property data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractProperty(f, statusElId, giftIndex, docType), docType: 'property' });
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the property document. Please try a clearer image.</span>';
        }
    } catch (e) {
        console.error('Property upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}


// ===========================================================================
// GLOBAL IDENTITY MODAL — available from all wizard steps
// ===========================================================================

const RELATIONSHIP_OPTIONS = [
    'Testator', 'Spouse', 'Husband', 'Wife',
    'Son', 'Daughter', 'Father', 'Mother',
    'Brother', 'Sister', 'Grandson', 'Granddaughter',
    'Grandfather', 'Grandmother',
    'Father-in-law', 'Mother-in-law',
    'Son-in-law', 'Daughter-in-law',
    'Brother-in-law', 'Sister-in-law',
    'Stepson', 'Stepdaughter',
    'Adopted Son', 'Adopted Daughter',
    'Uncle', 'Aunt', 'Nephew', 'Niece',
    'Cousin', 'Friend', 'Business Partner', 'Other'
];

function openAddIdentityModal(presetRelationship) {
    const modal = document.getElementById('identity-modal');
    if (!modal) return;
    document.getElementById('modal-title').textContent = 'Add Identity';
    document.getElementById('modal-person-id').value = '';
    document.getElementById('modal-full-name').value = '';
    document.getElementById('modal-nric-passport').value = '';
    document.getElementById('modal-nationality').value = 'Malaysian';
    document.getElementById('modal-address').value = '';
    document.getElementById('modal-passport-expiry').value = '';
    document.getElementById('passport-expiry-field').classList.add('hidden');
    document.getElementById('modal-dob').value = '';
    document.getElementById('modal-gender').value = '';
    document.getElementById('modal-email').value = '';
    document.getElementById('modal-phone').value = '';
    document.getElementById('modal-nric-upload').value = '';
    document.getElementById('modal-nric-status').innerHTML = '';
    // Hide banners
    const unsavedBanner = document.getElementById('modal-unsaved-banner');
    if (unsavedBanner) unsavedBanner.classList.add('hidden');
    const dupBanner = document.getElementById('modal-duplicate-banner');
    if (dupBanner) dupBanner.classList.add('hidden');
    // Reset save button
    const saveBtn = document.getElementById('modal-save-btn');
    if (saveBtn) {
        saveBtn.classList.remove('bg-green-600', 'hover:bg-green-700', 'animate-pulse', 'ring-2', 'ring-green-400');
        saveBtn.classList.add('bg-primary-600', 'hover:bg-primary-700');
        saveBtn.textContent = 'Save Identity';
    }
    // Set relationship
    const relSelect = document.getElementById('modal-relationship');
    const relOther = document.getElementById('modal-relationship-other');
    if (relSelect) {
        if (presetRelationship) {
            relSelect.value = presetRelationship;
        } else if (window._personRegistry && window._personRegistry.length === 0) {
            relSelect.value = 'Testator';
        } else {
            relSelect.value = '';
        }
        toggleRelationshipOther();
    }
    if (relOther) relOther.value = '';
    // Reset document preview
    hideDocumentPreview();
    modal.classList.remove('hidden');
    // Show address suggestions from existing identities
    showAddressSuggestions();
}

function openEditIdentityModal(personId) {
    const person = (window._personRegistry || []).find(p => p.id === personId);
    if (!person) return;
    const modal = document.getElementById('identity-modal');
    if (!modal) return;
    document.getElementById('modal-title').textContent = 'Edit Identity';
    document.getElementById('modal-person-id').value = personId;
    document.getElementById('modal-full-name').value = person.full_name || '';
    document.getElementById('modal-nric-passport').value = person.nric_passport || '';
    document.getElementById('modal-nationality').value = person.nationality || 'Malaysian';
    document.getElementById('modal-address').value = person.address || '';
    document.getElementById('modal-dob').value = person.date_of_birth ? convertDateForInput(person.date_of_birth) : '';
    document.getElementById('modal-gender').value = person.gender || '';
    document.getElementById('modal-email').value = person.email || '';
    document.getElementById('modal-phone').value = person.phone || '';
    // Passport expiry
    if (person.passport_expiry) {
        document.getElementById('passport-expiry-field').classList.remove('hidden');
        document.getElementById('modal-passport-expiry').value = convertDateForInput(person.passport_expiry);
    } else {
        document.getElementById('passport-expiry-field').classList.add('hidden');
        document.getElementById('modal-passport-expiry').value = '';
    }
    // Relationship
    const relSelect = document.getElementById('modal-relationship');
    const relOther = document.getElementById('modal-relationship-other');
    if (relSelect) {
        const rel = person.relationship || '';
        if (RELATIONSHIP_OPTIONS.includes(rel)) {
            relSelect.value = rel;
        } else if (rel) {
            relSelect.value = 'Other';
            if (relOther) relOther.value = rel;
        } else {
            relSelect.value = '';
        }
        toggleRelationshipOther();
    }
    document.getElementById('modal-nric-upload').value = '';
    document.getElementById('modal-nric-status').innerHTML = '';
    // Show document preview if identity has a linked document
    if (person.document_id) {
        showDocumentPreview(person.document_id, person.full_name + ' - IC/Passport');
    } else {
        hideDocumentPreview();
    }
    modal.classList.remove('hidden');
    // Show address suggestions from existing identities
    showAddressSuggestions();
}

function closeIdentityModal() {
    const modal = document.getElementById('identity-modal');
    if (modal) modal.classList.add('hidden');
    // Hide banners
    const unsaved = document.getElementById('modal-unsaved-banner');
    if (unsaved) unsaved.classList.add('hidden');
    const dup = document.getElementById('modal-duplicate-banner');
    if (dup) dup.classList.add('hidden');
    // Hide address suggestions
    hideAddressSuggestions();
    // Hide document preview
    hideDocumentPreview();
    // Reset save button style
    const saveBtn = document.getElementById('modal-save-btn');
    if (saveBtn) {
        saveBtn.classList.remove('bg-green-600', 'hover:bg-green-700', 'animate-pulse', 'ring-2', 'ring-green-400');
        saveBtn.classList.add('bg-primary-600', 'hover:bg-primary-700');
        saveBtn.textContent = 'Save Identity';
    }
}

/**
 * Find existing identity by NRIC/Passport number.
 * @param {string} nric - The NRIC or passport number to search for
 * @param {string} excludeId - Person ID to exclude (for edit mode)
 * @returns {object|null} The matching person or null
 */
function findDuplicateNRIC(nric, excludeId) {
    if (!nric || !window._personRegistry) return null;
    const normalized = nric.replace(/[-\s]/g, '').toUpperCase();
    return window._personRegistry.find(p => {
        if (excludeId && p.id === excludeId) return false;
        const pNric = (p.nric_passport || '').replace(/[-\s]/g, '').toUpperCase();
        return pNric === normalized;
    }) || null;
}

/**
 * Show unsaved data banner and highlight save button after OCR fill.
 */
function showUnsavedBanner() {
    const banner = document.getElementById('modal-unsaved-banner');
    if (banner) banner.classList.remove('hidden');
    // Highlight save button
    const saveBtn = document.getElementById('modal-save-btn');
    if (saveBtn) {
        saveBtn.classList.remove('bg-primary-600', 'hover:bg-primary-700');
        saveBtn.classList.add('bg-green-600', 'hover:bg-green-700', 'ring-2', 'ring-green-400');
        saveBtn.textContent = '✓ Save Identity';
    }
    // Scroll save button into view
    if (saveBtn) saveBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Show clickable address suggestions from existing identities.
 * Allows user to pick an existing address instead of typing manually.
 * Called after OCR fills the modal or when the modal opens.
 */
function showAddressSuggestions() {
    const container = document.getElementById('address-suggestions');
    const list = document.getElementById('address-suggestions-list');
    if (!container || !list) return;

    // Get unique addresses from existing identities
    const currentPersonId = document.getElementById('modal-person-id').value;
    const addresses = [];
    const seen = new Set();
    (window._personRegistry || []).forEach(p => {
        if (currentPersonId && p.id == currentPersonId) return; // skip self
        if (!p.address || !p.address.trim()) return;
        const normalized = p.address.trim().toLowerCase();
        if (seen.has(normalized)) return;
        seen.add(normalized);
        addresses.push({ name: p.full_name, relationship: p.relationship || '', address: p.address.trim() });
    });

    if (addresses.length === 0) {
        container.classList.add('hidden');
        return;
    }

    list.innerHTML = '';
    addresses.forEach(a => {
        const shortAddr = a.address.replace(/\n/g, ', ');
        const displayAddr = shortAddr.length > 60 ? shortAddr.substring(0, 60) + '...' : shortAddr;
        const label = a.relationship ? `${a.name} (${a.relationship})` : a.name;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'w-full text-left px-3 py-2 border border-gray-200 rounded-lg hover:bg-primary-50 hover:border-primary-300 transition-colors text-xs';
        btn.innerHTML = `<span class="font-medium text-gray-700">${label}</span><br><span class="text-gray-500">${displayAddr}</span>`;
        btn.onclick = function() {
            document.getElementById('modal-address').value = a.address;
            // Highlight the address field briefly
            const addrField = document.getElementById('modal-address');
            addrField.classList.add('bg-green-50', 'border-green-400');
            setTimeout(() => { addrField.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
        };
        list.appendChild(btn);
    });
    container.classList.remove('hidden');
}

/**
 * Hide address suggestions.
 */
function hideAddressSuggestions() {
    const container = document.getElementById('address-suggestions');
    if (container) container.classList.add('hidden');
}

/**
 * Check for duplicate and switch to edit mode if found.
 * @param {string} nricNumber - The scanned NRIC number
 */
function checkAndHandleDuplicate(nricNumber) {
    const currentId = document.getElementById('modal-person-id').value;
    const existing = findDuplicateNRIC(nricNumber, currentId);
    const dupBanner = document.getElementById('modal-duplicate-banner');
    if (existing) {
        // Switch to edit mode for the existing person
        document.getElementById('modal-person-id').value = existing.id;
        document.getElementById('modal-title').textContent = 'Update Identity';
        if (dupBanner) {
            const msg = document.getElementById('duplicate-message');
            if (msg) msg.textContent = `This NRIC already exists for "${existing.full_name}". Will update existing record.`;
            dupBanner.classList.remove('hidden');
        }
    } else {
        if (dupBanner) dupBanner.classList.add('hidden');
    }
}

function toggleRelationshipOther() {
    const relSelect = document.getElementById('modal-relationship');
    const otherWrap = document.getElementById('relationship-other-wrap');
    if (relSelect && otherWrap) {
        otherWrap.classList.toggle('hidden', relSelect.value !== 'Other');
    }
}

async function saveIdentityGlobal() {
    const personId = document.getElementById('modal-person-id').value;
    const fullName = document.getElementById('modal-full-name').value.trim();
    const nricPassport = document.getElementById('modal-nric-passport').value.trim();
    if (!fullName || !nricPassport) {
        alert('Name and NRIC/Passport are required.');
        return;
    }

    // Check for duplicate NRIC before creating new identity
    if (!personId) {
        const existing = findDuplicateNRIC(nricPassport);
        if (existing) {
            const update = confirm(`An identity with this NRIC/Passport already exists:\n\n${existing.full_name} (${existing.nric_passport})\n\nDo you want to UPDATE the existing record instead?`);
            if (update) {
                // Switch to edit mode
                document.getElementById('modal-person-id').value = existing.id;
            } else {
                return; // Cancel save
            }
        }
    }

    // Re-read personId in case it was updated by duplicate check
    const finalPersonId = document.getElementById('modal-person-id').value;

    const dobRaw = document.getElementById('modal-dob').value;
    let dob = '';
    if (dobRaw) {
        const parts = dobRaw.split('-');
        if (parts.length === 3 && parts[0].length === 4) dob = `${parts[2]}-${parts[1]}-${parts[0]}`;
        else dob = dobRaw;
    }

    const expiryRaw = document.getElementById('modal-passport-expiry').value;
    let expiry = '';
    if (expiryRaw) {
        const parts = expiryRaw.split('-');
        if (parts.length === 3 && parts[0].length === 4) expiry = `${parts[2]}-${parts[1]}-${parts[0]}`;
        else expiry = expiryRaw;
    }

    // Get relationship
    const relSelect = document.getElementById('modal-relationship');
    let relationship = relSelect ? relSelect.value : '';
    if (relationship === 'Other') {
        const otherInput = document.getElementById('modal-relationship-other');
        relationship = otherInput ? otherInput.value.trim() : '';
    }

    // Get document ID if a document was uploaded
    const documentId = document.getElementById('modal-document-id')?.value || '';

    const data = {
        full_name: fullName,
        nric_passport: nricPassport,
        nationality: document.getElementById('modal-nationality').value.trim() || 'Malaysian',
        address: document.getElementById('modal-address').value.trim(),
        passport_expiry: expiry,
        date_of_birth: dob,
        gender: document.getElementById('modal-gender').value,
        email: document.getElementById('modal-email').value.trim(),
        phone: document.getElementById('modal-phone').value.trim(),
        relationship: relationship,
        document_id: documentId,
    };

    const url = finalPersonId ? `/api/persons/${finalPersonId}` : '/api/persons';
    const method = finalPersonId ? 'PUT' : 'POST';

    try {
        const resp = await fetch(url, {
            method: method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        const result = await resp.json();
        if (result.ok) {
            // Update person registry
            if (result.person) {
                if (finalPersonId) {
                    const idx = window._personRegistry.findIndex(p => p.id === finalPersonId);
                    if (idx >= 0) window._personRegistry[idx] = result.person;
                } else {
                    window._personRegistry.push(result.person);
                }
            }
            refreshPersonDropdowns();
            // Dispatch event for step1 to refresh its card list
            document.dispatchEvent(new CustomEvent('identitySaved', { detail: result.person }));

            // Ask to add another
            closeIdentityModal();
            showAddAnotherPrompt();
        } else {
            alert(result.error || 'Failed to save identity.');
        }
    } catch (e) {
        alert('Failed to save identity.');
    }
}

function showAddAnotherPrompt() {
    const prompt = document.getElementById('add-another-prompt');
    if (prompt) {
        // Update count summary
        const countEl = document.getElementById('identity-count-summary');
        if (countEl) {
            const counts = {};
            for (const p of window._personRegistry) {
                const r = p.relationship || 'Unspecified';
                counts[r] = (counts[r] || 0) + 1;
            }
            const parts = Object.entries(counts).map(([k, v]) => `${k}: ${v}`);
            countEl.textContent = `${window._personRegistry.length} identities added (${parts.join(', ')})`;
        }
        prompt.classList.remove('hidden');
    }
}

function closeAddAnotherPrompt() {
    const prompt = document.getElementById('add-another-prompt');
    if (prompt) prompt.classList.add('hidden');
}

function addAnotherIdentity() {
    closeAddAnotherPrompt();
    openAddIdentityModal();
}

/**
 * Calculate age from person's date_of_birth (DD-MM-YYYY) or nric_passport (YYMMDD-SS-NNNN).
 * Returns null if age cannot be determined.
 */
function getPersonAge(person) {
    const today = new Date();
    let birthYear, birthMonth, birthDay;

    // Try date_of_birth first (formats: DD-MM-YYYY or YYYY-MM-DD)
    if (person.date_of_birth) {
        const dob = person.date_of_birth;
        if (dob.includes('-') && dob.length >= 8) {
            const parts = dob.split('-');
            if (parts[0].length === 4) {
                birthYear = parseInt(parts[0]);
                birthMonth = parseInt(parts[1]);
                birthDay = parseInt(parts[2]);
            } else {
                birthDay = parseInt(parts[0]);
                birthMonth = parseInt(parts[1]);
                birthYear = parseInt(parts[2]);
            }
        }
    }

    // Fall back to NRIC (YYMMDD-SS-NNNN or 12-digit)
    if (!birthYear && person.nric_passport) {
        const nric = person.nric_passport.replace(/-/g, '');
        if (/^\d{12}$/.test(nric)) {
            const yy = parseInt(nric.substring(0, 2));
            birthMonth = parseInt(nric.substring(2, 4));
            birthDay = parseInt(nric.substring(4, 6));
            const currentYY = today.getFullYear() % 100;
            birthYear = yy > (currentYY + 5) ? 1900 + yy : 2000 + yy;
        }
    }

    if (!birthYear || !birthMonth || !birthDay) return null;
    if (birthMonth < 1 || birthMonth > 12 || birthDay < 1 || birthDay > 31) return null;

    let age = today.getFullYear() - birthYear;
    const monthDiff = (today.getMonth() + 1) - birthMonth;
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDay)) {
        age--;
    }
    return age;
}

/**
 * Get the testator's person ID from the registry or session.
 */
function getTestatorId() {
    // Check registry for person with relationship "Testator"
    for (const p of window._personRegistry) {
        if (p.relationship && p.relationship.toLowerCase() === 'testator') {
            return p.id;
        }
    }
    // Check step 2 selector if on that page
    const testatorSelect = document.getElementById('testator-identity');
    if (testatorSelect && testatorSelect.value) {
        return testatorSelect.value;
    }
    // Check session data passed from server
    if (window._testatorPersonId) {
        return window._testatorPersonId;
    }
    return null;
}

/**
 * Build filtered person options HTML for dynamic dropdown creation.
 * @param {string} role - "testator", "executor", "guardian", "beneficiary", "trustee"
 * @returns {string} HTML string of <option> elements
 */
function buildFilteredPersonOptions(role) {
    const testatorId = getTestatorId();
    let html = '<option value="">-- Select an identity --</option>';
    for (const p of window._personRegistry) {
        if (!_personPassesFilter(p, role, testatorId)) continue;
        html += `<option value="${p.id}" data-name="${p.full_name}" data-nric="${p.nric_passport}" data-address="${p.address || ''}" data-nationality="${p.nationality || 'Malaysian'}" data-dob="${p.date_of_birth || ''}" data-gender="${p.gender || ''}" data-email="${p.email || ''}" data-phone="${p.phone || ''}" data-relationship="${p.relationship || ''}">${p.full_name} (${p.nric_passport})${p.relationship ? ' [' + p.relationship + ']' : ''}</option>`;
    }
    return html;
}

/**
 * Check if a person passes the filter for a given role.
 */
function _personPassesFilter(person, role, testatorId) {
    if (role === 'testator') {
        const age = getPersonAge(person);
        if (age !== null && age < 18) return false;
    } else if (role === 'executor' || role === 'trustee' || role === 'guardian') {
        const age = getPersonAge(person);
        if (age !== null && age < 18) return false;
        if (testatorId && person.id === testatorId) return false;
    } else if (role === 'beneficiary') {
        if (testatorId && person.id === testatorId) return false;
    }
    return true;
}

/**
 * Refresh all <select> elements with class "person-select" from window._personRegistry.
 * Preserves currently selected value. Filters based on data-role attribute.
 */
function refreshPersonDropdowns() {
    const testatorId = getTestatorId();

    document.querySelectorAll('select.person-select').forEach(sel => {
        const currentVal = sel.value;
        const role = sel.dataset.role || '';

        // Keep the first placeholder option
        const placeholder = sel.querySelector('option[value=""]');
        sel.innerHTML = '';
        if (placeholder) {
            sel.appendChild(placeholder);
        } else {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '-- Select an identity --';
            sel.appendChild(opt);
        }
        for (const p of window._personRegistry) {
            // Apply filters based on role
            if (!_personPassesFilter(p, role, testatorId)) continue;

            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = `${p.full_name} (${p.nric_passport})${p.relationship ? ' [' + p.relationship + ']' : ''}`;
            opt.dataset.name = p.full_name;
            opt.dataset.nric = p.nric_passport;
            opt.dataset.address = p.address || '';
            opt.dataset.nationality = p.nationality || 'Malaysian';
            opt.dataset.dob = p.date_of_birth || '';
            opt.dataset.gender = p.gender || '';
            opt.dataset.email = p.email || '';
            opt.dataset.phone = p.phone || '';
            opt.dataset.relationship = p.relationship || '';
            if (p.id === currentVal) opt.selected = true;
            sel.appendChild(opt);
        }
    });
}

/**
 * Show identity info for any person-select dropdown.
 * Used across steps 3, 4, 5, 8.
 */
function showIdentityInfo(selectEl, infoId) {
    const opt = selectEl.options[selectEl.selectedIndex];
    const info = document.getElementById(infoId);
    if (!info) return;
    if (!opt || !opt.value) { info.classList.add('hidden'); info.innerHTML = ''; return; }
    info.innerHTML = `${opt.dataset.name || ''} &middot; ${opt.dataset.nric || ''} &middot; ${opt.dataset.address || 'N/A'}`;
    info.classList.remove('hidden');

    // Auto-populate relationship field from identity's relationship
    if (opt.dataset.relationship) {
        const entry = selectEl.closest('.executor-entry, .guardian-entry, .beneficiary-entry, [class*="-entry"]');
        if (entry) {
            const relInput = entry.querySelector('input[name*="_relationship_"]');
            if (relInput && !relInput.value) {
                relInput.value = opt.dataset.relationship;
            }
        }
    }
}

/**
 * Camera + OCR for identity modal (global version).
 */
function openCameraForNRIC() {
    openCameraViewfinder((file) => {
        uploadNRICForIdentity(file);
    }, 'nric');
}

async function uploadNRICForIdentity(inputOrFile) {
    let file;
    if (inputOrFile instanceof File) {
        file = inputOrFile;
    } else {
        file = inputOrFile.files[0];
    }
    if (!file) return;

    const validation = validateFile(file);
    if (!validation.valid) {
        const statusEl = document.getElementById('modal-nric-status');
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    const statusEl = document.getElementById('modal-nric-status');
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/nric', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = 'Could not scan the document. Please try again with a clearer image.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            const extracted = data.extracted;
            const isPassport = (extracted.doc_type || '').toLowerCase() === 'passport';
            if (!isPassport) {
                delete extracted.passport_expiry;
            }
            delete extracted.doc_type;

            showOCRConfirmation(extracted, file, (confirmed) => {
                // Apply confirmed data to identity modal fields
                if (confirmed.full_name) document.getElementById('modal-full-name').value = confirmed.full_name;
                if (confirmed.nric_number) document.getElementById('modal-nric-passport').value = confirmed.nric_number;
                if (confirmed.address) document.getElementById('modal-address').value = confirmed.address;
                if (confirmed.nationality) document.getElementById('modal-nationality').value = confirmed.nationality;
                if (confirmed.gender) document.getElementById('modal-gender').value = confirmed.gender;
                if (confirmed.date_of_birth) {
                    const dateVal = convertDateForInput(confirmed.date_of_birth);
                    if (dateVal) document.getElementById('modal-dob').value = dateVal;
                }
                if (isPassport && confirmed.passport_expiry) {
                    document.getElementById('passport-expiry-field').classList.remove('hidden');
                    const expiryVal = convertDateForInput(confirmed.passport_expiry);
                    if (expiryVal) document.getElementById('modal-passport-expiry').value = expiryVal;
                }
                // Store document ID and show document preview
                if (data.document_id) {
                    showDocumentPreview(data.document_id, file.name || 'IC/Passport scan', file);
                }
                // Check for duplicate NRIC — switch to update mode if exists
                if (confirmed.nric_number) {
                    checkAndHandleDuplicate(confirmed.nric_number);
                }
                // Show unsaved banner + highlight save button
                showUnsavedBanner();
                // Show address suggestions if address is empty or low quality
                showAddressSuggestions();
                if (statusEl) statusEl.innerHTML = '';
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadNRICForIdentity(f), docType: 'nric' });
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the document. Please try a clearer image.</span>';
        }
    } catch (e) {
        console.error('NRIC upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}


// ===========================================================================
// Asset Document OCR Upload
// ===========================================================================

async function uploadAndExtractAsset(inputOrFile, statusElId, giftIndex) {
    let file = (inputOrFile instanceof File) ? inputOrFile : inputOrFile.files[0];
    if (!file) return;

    const validation = validateFile(file);
    const statusEl = document.getElementById(statusElId);
    if (!validation.valid) {
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning financial document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/asset', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = 'Could not read the financial document. Please try again with a clearer image.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            showOCRConfirmation(data.extracted, file, (confirmed) => {
                if (confirmed.assets && confirmed.assets.length > 0) {
                    const a = confirmed.assets[0];
                    if (a.institution) setFieldValue(`gift_fin_institution_${giftIndex}`, a.institution);
                    if (a.account_number) setFieldValue(`gift_fin_account_${giftIndex}`, a.account_number);
                    if (a.type) {
                        const tf = document.querySelector(`[name="gift_fin_type_${giftIndex}"]`);
                        if (tf) { tf.value = a.type; tf.classList.add('bg-yellow-50'); }
                    }
                    if (a.description) setFieldValue(`gift_fin_desc_${giftIndex}`, a.description);
                }
                const fr = document.querySelector(`[name="gift_type_${giftIndex}"][value="financial"]`);
                if (fr) { fr.checked = true; switchGiftType(giftIndex, 'financial'); }
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Asset data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractAsset(f, statusElId, giftIndex), docType: 'financial' });
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the financial document. Please try a clearer image.</span>';
        }
    } catch (e) {
        console.error('Asset upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}
