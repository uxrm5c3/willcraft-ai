/* WillCraft AI - Wizard JavaScript */

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

    // Check size
    const sizeMB = file.size / (1024 * 1024);
    if (sizeMB > MAX_FILE_SIZE_MB) {
        return { valid: false, error: `File too large (${sizeMB.toFixed(1)}MB). Maximum is ${MAX_FILE_SIZE_MB}MB.` };
    }

    // Check type — be lenient since mobile browsers sometimes report empty types
    if (file.type && !ALLOWED_IMAGE_TYPES.includes(file.type) && !ALLOWED_DOC_TYPES.includes(file.type)) {
        // Also check extension as fallback
        const ext = file.name.split('.').pop().toLowerCase();
        const allowedExts = ['png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc', 'heic', 'heif', 'webp', 'bmp', 'tiff', 'tif'];
        if (!allowedExts.includes(ext)) {
            return { valid: false, error: `File type not supported: .${ext}. Please use JPG, PNG, PDF, or HEIC.` };
        }
    }

    return { valid: true };
}

// ===========================================================================
// CAMERA VIEWFINDER MODAL SYSTEM
// ===========================================================================

let _cameraStream = null;
let _cameraFacingMode = 'environment'; // 'environment' = back camera, 'user' = front
let _cameraCapturedBlob = null;
let _cameraCallback = null;  // Called with the captured File
let _cameraDocType = 'document'; // 'nric', 'property', 'financial'

/**
 * Open the camera viewfinder modal.
 * @param {Function} callback - Called with the captured File object
 * @param {string} docType - Type of document being scanned: 'nric', 'property', 'financial'
 */
async function openCameraViewfinder(callback, docType) {
    _cameraCallback = callback;
    _cameraDocType = docType || 'document';
    _cameraCapturedBlob = null;

    // Set guide text based on document type
    const titleEl = document.getElementById('camera-title');
    const subtitleEl = document.getElementById('camera-subtitle');
    const guideTextEl = document.getElementById('camera-guide-text');
    const guideBoxEl = document.getElementById('camera-guide-box');

    if (docType === 'nric') {
        titleEl.textContent = 'Scan NRIC / Passport';
        subtitleEl.textContent = 'Position your ID card within the frame';
        guideTextEl.textContent = 'Place NRIC / Passport here';
        guideBoxEl.style.aspectRatio = '1.6/1';
    } else if (docType === 'property') {
        titleEl.textContent = 'Scan Property Document';
        subtitleEl.textContent = 'Position title/cukai/SPA document in frame';
        guideTextEl.textContent = 'Place document here';
        guideBoxEl.style.aspectRatio = '1/1.4';
    } else if (docType === 'financial') {
        titleEl.textContent = 'Scan Financial Document';
        subtitleEl.textContent = 'Position bank statement in frame';
        guideTextEl.textContent = 'Place document here';
        guideBoxEl.style.aspectRatio = '1/1.4';
    } else {
        titleEl.textContent = 'Scan Document';
        subtitleEl.textContent = 'Position document within the frame';
        guideTextEl.textContent = 'Align document here';
        guideBoxEl.style.aspectRatio = '1.6/1';
    }

    // Show modal
    document.getElementById('camera-modal').classList.remove('hidden');
    document.getElementById('camera-controls').classList.remove('hidden');
    document.getElementById('camera-preview-controls').classList.add('hidden');
    document.getElementById('camera-video').classList.remove('hidden');
    document.getElementById('camera-preview').classList.add('hidden');

    // Start camera
    await startCamera();
}

async function startCamera() {
    // Stop any existing stream first
    stopCameraStream();

    try {
        const constraints = {
            video: {
                facingMode: _cameraFacingMode,
                width: { ideal: 1920 },
                height: { ideal: 1080 }
            }
        };

        _cameraStream = await navigator.mediaDevices.getUserMedia(constraints);
        const video = document.getElementById('camera-video');
        video.srcObject = _cameraStream;
        await video.play();
    } catch (err) {
        console.error('Camera access error:', err);
        // Fall back to file picker if camera not available
        closeCameraModal();
        // Create a temporary file input and trigger it
        const tempInput = document.createElement('input');
        tempInput.type = 'file';
        tempInput.accept = 'image/*';
        tempInput.onchange = function() {
            if (tempInput.files[0] && _cameraCallback) {
                _cameraCallback(tempInput.files[0]);
            }
        };
        tempInput.click();
    }
}

function stopCameraStream() {
    if (_cameraStream) {
        _cameraStream.getTracks().forEach(track => track.stop());
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

    // Set canvas to video resolution
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    // Draw video frame to canvas
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);

    // Convert to blob
    canvas.toBlob((blob) => {
        _cameraCapturedBlob = blob;

        // Show preview
        const url = URL.createObjectURL(blob);
        preview.src = url;
        preview.classList.remove('hidden');
        video.classList.add('hidden');

        // Switch controls
        document.getElementById('camera-controls').classList.add('hidden');
        document.getElementById('camera-preview-controls').classList.remove('hidden');

        // Pause camera
        stopCameraStream();
    }, 'image/jpeg', 0.92);
}

function retakePhoto() {
    _cameraCapturedBlob = null;

    // Switch back to live view
    document.getElementById('camera-preview').classList.add('hidden');
    document.getElementById('camera-video').classList.remove('hidden');
    document.getElementById('camera-controls').classList.remove('hidden');
    document.getElementById('camera-preview-controls').classList.add('hidden');

    // Restart camera
    startCamera();
}

function usePhoto() {
    if (!_cameraCapturedBlob || !_cameraCallback) return;

    // Create a File from the blob
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `${_cameraDocType}_${timestamp}.jpg`;
    const file = new File([_cameraCapturedBlob], filename, { type: 'image/jpeg' });

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
    document.getElementById('camera-modal').classList.add('hidden');
    _cameraCapturedBlob = null;
    // Reset gallery input
    document.getElementById('camera-gallery-input').value = '';
}


// ===========================================================================
// OCR CONFIRMATION MODAL SYSTEM
// ===========================================================================

let _ocrPendingData = null;       // Extracted data waiting for confirmation
let _ocrPendingCallback = null;   // Function to call with confirmed data
let _ocrRetryInputEl = null;      // File input to re-trigger for retry
let _ocrRetryArgs = null;         // Arguments for retry
let _ocrRetryCamera = null;       // Camera callback for retry via camera

// Field label mapping for human-readable display
const OCR_FIELD_LABELS = {
    full_name: 'Full Name',
    nric_number: 'NRIC / Passport No.',
    date_of_birth: 'Date of Birth',
    address: 'Address',
    gender: 'Gender',
    nationality: 'Nationality',
    passport_expiry: 'Passport Expiry',
    property_address: 'Property Address',
    title_type: 'Title Type',
    lot_number: 'Lot Number',
    title_number: 'Title / Hakmilik Number',
    bandar_pekan: 'Bandar / Pekan',
    mukim: 'Mukim',
    daerah: 'Daerah (District)',
    negeri: 'Negeri (State)',
    property_description: 'Description',
    institution: 'Institution',
    account_number: 'Account Number',
    type: 'Asset Type',
    description: 'Description',
    account_holder_name: 'Account Holder',
    account_holder_nric: 'Account Holder NRIC',
};

function formatFieldLabel(key) {
    return OCR_FIELD_LABELS[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/**
 * Show OCR confirmation modal with extracted data.
 * @param {Object} extracted - The extracted data from OCR
 * @param {File} imageFile - The uploaded file (for preview)
 * @param {Function} callback - Called with confirmed (possibly edited) data
 * @param {HTMLInputElement} retryInputEl - The file input for retry
 * @param {Array} retryArgs - Arguments for the retry function call
 * @param {Object} retryCameraInfo - { callback, docType } for camera retry
 */
function showOCRConfirmation(extracted, imageFile, callback, retryInputEl, retryArgs, retryCameraInfo) {
    _ocrPendingData = extracted;
    _ocrPendingCallback = callback;
    _ocrRetryInputEl = retryInputEl;
    _ocrRetryArgs = retryArgs;
    _ocrRetryCamera = retryCameraInfo || null;

    // Show image preview
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

    // Check for error or empty data
    const hasError = extracted.error || Object.values(extracted).every(v => !v || v === '');
    const warningEl = document.getElementById('ocr-warning');
    if (hasError) {
        warningEl.classList.remove('hidden');
        warningEl.innerHTML = '<p class="text-sm text-amber-700"><strong>⚠ Image may be unclear:</strong> Some fields could not be read. Please verify and correct, or upload a clearer image.</p>';
    } else {
        // Check if many fields are empty (low confidence)
        const nonEmptyCount = Object.entries(extracted).filter(([k, v]) => v && v !== '' && k !== 'error' && k !== 'raw').length;
        const totalKeys = Object.keys(extracted).filter(k => k !== 'error' && k !== 'raw').length;
        if (nonEmptyCount < totalKeys * 0.5) {
            warningEl.classList.remove('hidden');
            warningEl.innerHTML = '<p class="text-sm text-amber-700"><strong>⚠ Low confidence:</strong> Many fields are empty. The image may be unclear. Please review or upload a better image.</p>';
        } else {
            warningEl.classList.add('hidden');
        }
    }

    // Populate editable fields
    const container = document.getElementById('ocr-fields-container');
    container.innerHTML = '';
    for (const [key, value] of Object.entries(extracted)) {
        if (key === 'error' || key === 'raw' || key === 'assets') continue;
        const label = formatFieldLabel(key);
        const isEmpty = !value || value.toString().trim() === '';
        const borderClass = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
        container.innerHTML += `
            <div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                <label class="text-sm font-medium text-gray-700 sm:w-40 shrink-0">${label}</label>
                <input name="ocr-field-${key}" value="${(value || '').toString().replace(/"/g, '&quot;')}"
                       class="flex-1 px-3 py-1.5 border ${borderClass} rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500">
            </div>`;
    }

    // Handle assets array (for financial OCR)
    if (extracted.assets && extracted.assets.length > 0) {
        const a = extracted.assets[0];
        for (const [key, value] of Object.entries(a)) {
            const label = formatFieldLabel(key);
            const isEmpty = !value || value.toString().trim() === '';
            const borderClass = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
            container.innerHTML += `
                <div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
                    <label class="text-sm font-medium text-gray-700 sm:w-40 shrink-0">${label}</label>
                    <input name="ocr-field-asset_${key}" value="${(value || '').toString().replace(/"/g, '&quot;')}"
                           class="flex-1 px-3 py-1.5 border ${borderClass} rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500">
                </div>`;
        }
    }

    // Show modal
    document.getElementById('ocr-confirm-modal').classList.remove('hidden');
}

function applyOCRData() {
    // Collect edited values from modal fields
    const fields = document.querySelectorAll('#ocr-fields-container input');
    const data = {};
    const assetData = {};
    fields.forEach(f => {
        const name = f.name;
        if (name.startsWith('ocr-field-asset_')) {
            const key = name.replace('ocr-field-asset_', '');
            assetData[key] = f.value;
        } else if (name.startsWith('ocr-field-')) {
            const key = name.replace('ocr-field-', '');
            data[key] = f.value;
        }
    });
    // Re-attach assets if present
    if (Object.keys(assetData).length > 0) {
        data.assets = [assetData];
    }

    // Call the callback with confirmed data
    if (_ocrPendingCallback) _ocrPendingCallback(data);
    closeOCRModal();
}

function retryOCRUpload() {
    closeOCRModal();

    // If we have camera info, open camera viewfinder
    if (_ocrRetryCamera) {
        openCameraViewfinder(_ocrRetryCamera.callback, _ocrRetryCamera.docType);
        return;
    }

    // Otherwise re-trigger the file input so user can pick a new file
    if (_ocrRetryInputEl) {
        _ocrRetryInputEl.value = '';
        _ocrRetryInputEl.click();
    }
}

function closeOCRModal() {
    document.getElementById('ocr-confirm-modal').classList.add('hidden');
    _ocrPendingData = null;
    _ocrPendingCallback = null;
}

// ===========================================================================
// NRIC / Passport OCR Upload (with confirmation)
// ===========================================================================

async function uploadAndExtractNRIC(inputOrFile, statusElId, fieldMapping) {
    let file;
    if (inputOrFile instanceof File) {
        file = inputOrFile;
    } else {
        file = inputOrFile.files[0];
    }
    if (!file) return;

    // Validate file
    const validation = validateFile(file);
    if (!validation.valid) {
        const statusEl = document.getElementById(statusElId);
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/nric', { method: 'POST', body: formData });
        if (!resp.ok) {
            const errText = await resp.text();
            let errMsg = 'Server error';
            try { errMsg = JSON.parse(errText).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            // Show confirmation modal instead of auto-filling
            showOCRConfirmation(data.extracted, file, (confirmed) => {
                // Apply confirmed data to form fields
                if (fieldMapping.name && confirmed.full_name) setValueIfEmpty(fieldMapping.name, confirmed.full_name);
                if (fieldMapping.nric && confirmed.nric_number) setValueIfEmpty(fieldMapping.nric, confirmed.nric_number);
                if (fieldMapping.address && confirmed.address) setValueIfEmpty(fieldMapping.address, confirmed.address);
                if (fieldMapping.dob && confirmed.date_of_birth) {
                    const dateVal = convertDateForInput(confirmed.date_of_birth);
                    if (dateVal) setValueIfEmpty(fieldMapping.dob, dateVal);
                }
                if (fieldMapping.gender && confirmed.gender) {
                    const genderField = document.querySelector(`[name="${fieldMapping.gender}"]`);
                    if (genderField) { genderField.value = confirmed.gender; genderField.classList.add('bg-yellow-50'); }
                }
                if (fieldMapping.nationality && confirmed.nationality) setValueIfEmpty(fieldMapping.nationality, confirmed.nationality);
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractNRIC(f, statusElId, fieldMapping), docType: 'nric' });
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('NRIC upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check your connection and try again.</span>';
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

// ===========================================================================
// Property Document OCR Upload (with confirmation)
// ===========================================================================

async function uploadAndExtractProperty(inputOrFile, statusElId, giftIndex, docType) {
    let file;
    if (inputOrFile instanceof File) {
        file = inputOrFile;
    } else {
        file = inputOrFile.files[0];
    }
    if (!file) return;

    // Validate file
    const validation = validateFile(file);
    if (!validation.valid) {
        const statusEl = document.getElementById(statusElId);
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning property document...</span>';

    const formData = new FormData();
    formData.append('file', file);
    if (docType) formData.append('doc_type', docType);

    try {
        const resp = await fetch('/api/ocr/property', { method: 'POST', body: formData });
        if (!resp.ok) {
            const errText = await resp.text();
            let errMsg = 'Server error';
            try { errMsg = JSON.parse(errText).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            // Show confirmation modal
            showOCRConfirmation(data.extracted, file, (confirmed) => {
                // Apply confirmed property data
                if (confirmed.property_address) setFieldValue(`gift_prop_address_${giftIndex}`, confirmed.property_address);
                if (confirmed.title_type) {
                    const ttField = document.querySelector(`[name="gift_prop_title_type_${giftIndex}"]`);
                    if (ttField) { ttField.value = confirmed.title_type; ttField.classList.add('bg-yellow-50'); }
                }
                if (confirmed.title_number) setFieldValue(`gift_prop_title_number_${giftIndex}`, confirmed.title_number);
                if (confirmed.lot_number) setFieldValue(`gift_prop_lot_number_${giftIndex}`, confirmed.lot_number);
                if (confirmed.bandar_pekan || confirmed.mukim) setFieldValue(`gift_prop_bandar_${giftIndex}`, confirmed.bandar_pekan || confirmed.mukim || '');
                if (confirmed.daerah) setFieldValue(`gift_prop_daerah_${giftIndex}`, confirmed.daerah);
                if (confirmed.negeri) {
                    const ngField = document.querySelector(`[name="gift_prop_negeri_${giftIndex}"]`);
                    if (ngField) { ngField.value = confirmed.negeri.toUpperCase(); ngField.classList.add('bg-yellow-50'); }
                }
                const propRadio = document.querySelector(`[name="gift_type_${giftIndex}"][value="property"]`);
                if (propRadio) { propRadio.checked = true; switchGiftType(giftIndex, 'property'); }
                updatePropertyPreview(giftIndex);
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Property data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractProperty(f, statusElId, giftIndex, docType), docType: 'property' });
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('Property upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check your connection and try again.</span>';
    }
}

// ===========================================================================
// Asset Document OCR Upload (with confirmation)
// ===========================================================================

async function uploadAndExtractAsset(inputOrFile, statusElId, giftIndex) {
    let file;
    if (inputOrFile instanceof File) {
        file = inputOrFile;
    } else {
        file = inputOrFile.files[0];
    }
    if (!file) return;

    // Validate file
    const validation = validateFile(file);
    if (!validation.valid) {
        const statusEl = document.getElementById(statusElId);
        if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${validation.error}</span>`;
        return;
    }

    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning financial document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/asset', { method: 'POST', body: formData });
        if (!resp.ok) {
            const errText = await resp.text();
            let errMsg = 'Server error';
            try { errMsg = JSON.parse(errText).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">📋 Review extracted data...</span>';

            // Show confirmation modal
            showOCRConfirmation(data.extracted, file, (confirmed) => {
                // Apply confirmed financial data
                if (confirmed.assets && confirmed.assets.length > 0) {
                    const a = confirmed.assets[0];
                    if (a.institution) setFieldValue(`gift_fin_institution_${giftIndex}`, a.institution);
                    if (a.account_number) setFieldValue(`gift_fin_account_${giftIndex}`, a.account_number);
                    if (a.type) {
                        const typeField = document.querySelector(`[name="gift_fin_type_${giftIndex}"]`);
                        if (typeField) { typeField.value = a.type; typeField.classList.add('bg-yellow-50'); }
                    }
                    if (a.description) setFieldValue(`gift_fin_desc_${giftIndex}`, a.description);
                }
                const finRadio = document.querySelector(`[name="gift_type_${giftIndex}"][value="financial"]`);
                if (finRadio) { finRadio.checked = true; switchGiftType(giftIndex, 'financial'); }
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Asset data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractAsset(f, statusElId, giftIndex), docType: 'financial' });
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('Asset upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check your connection and try again.</span>';
    }
}
