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
// CAMERA VIEWFINDER WITH IC/DOCUMENT GUIDE BOX
// ===========================================================================

let _cameraStream = null;
let _cameraFacingMode = 'environment';
let _cameraCapturedBlob = null;
let _cameraCallback = null;
let _cameraDocType = 'nric';

/**
 * Open camera viewfinder with document guide overlay.
 * @param {Function} callback - Called with captured File object
 * @param {string} docType - 'nric', 'property', 'financial'
 */
async function openCameraViewfinder(callback, docType) {
    _cameraCallback = callback;
    _cameraDocType = docType || 'nric';
    _cameraCapturedBlob = null;

    const titleEl = document.getElementById('camera-title');
    const subtitleEl = document.getElementById('camera-subtitle');
    const guideTextEl = document.getElementById('camera-guide-text');
    const cornersEl = document.getElementById('camera-corners');
    const maskCutout = document.getElementById('mask-cutout');
    const guideBorder = document.getElementById('guide-border');

    // Configure guide box shape based on document type
    if (docType === 'nric') {
        titleEl.textContent = 'Scan IC / Passport';
        subtitleEl.textContent = 'Fit your IC card inside the frame';
        guideTextEl.textContent = 'Place IC / Passport here';
        // IC card ratio: landscape rectangle
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','25%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','50%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','25%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','50%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:25%;width:90%;height:50%;position:absolute;'; }
    } else if (docType === 'property' || docType === 'financial') {
        titleEl.textContent = docType === 'property' ? 'Scan Property Document' : 'Scan Financial Document';
        subtitleEl.textContent = 'Fit the document inside the frame';
        guideTextEl.textContent = 'Place document here';
        // Document: taller rectangle
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','10%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','75%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','10%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','75%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:10%;width:90%;height:75%;position:absolute;'; }
    } else {
        titleEl.textContent = 'Scan Document';
        subtitleEl.textContent = 'Fit the document inside the frame';
        guideTextEl.textContent = 'Align document here';
        if (maskCutout) { maskCutout.setAttribute('x','5%'); maskCutout.setAttribute('y','20%'); maskCutout.setAttribute('width','90%'); maskCutout.setAttribute('height','55%'); }
        if (guideBorder) { guideBorder.setAttribute('x','5%'); guideBorder.setAttribute('y','20%'); guideBorder.setAttribute('width','90%'); guideBorder.setAttribute('height','55%'); }
        if (cornersEl) { cornersEl.style.cssText = 'left:5%;top:20%;width:90%;height:55%;position:absolute;'; }
    }

    // Show modal, reset state
    const modal = document.getElementById('camera-modal');
    modal.classList.remove('hidden');
    document.getElementById('camera-controls').classList.remove('hidden');
    document.getElementById('camera-preview-controls').classList.add('hidden');
    document.getElementById('camera-video').classList.remove('hidden');
    document.getElementById('camera-preview').classList.add('hidden');
    document.getElementById('camera-overlay').classList.remove('hidden');
    document.getElementById('camera-processing').classList.add('hidden');

    await startCamera();
}

async function startCamera() {
    stopCameraStream();
    try {
        _cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: _cameraFacingMode, width: { ideal: 1920 }, height: { ideal: 1080 } }
        });
        const video = document.getElementById('camera-video');
        video.srcObject = _cameraStream;
        await video.play();
    } catch (err) {
        console.error('Camera error:', err);
        closeCameraModal();
        // Fallback: open file picker
        const tmp = document.createElement('input');
        tmp.type = 'file';
        tmp.accept = 'image/*';
        tmp.capture = 'environment';
        tmp.onchange = function() {
            if (tmp.files[0] && _cameraCallback) _cameraCallback(tmp.files[0]);
        };
        tmp.click();
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
    document.getElementById('camera-modal').classList.add('hidden');
    document.getElementById('camera-processing').classList.add('hidden');
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
        container.innerHTML += `<div class="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
            <label class="text-sm font-medium text-gray-700 sm:w-40 shrink-0">${label}</label>
            <input name="ocr-field-${key}" value="${(value||'').toString().replace(/"/g,'&quot;')}"
                   class="flex-1 px-3 py-1.5 border ${bc} rounded-lg text-sm focus:ring-2 focus:ring-primary-500">
        </div>`;
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
    const fields = document.querySelectorAll('#ocr-fields-container input');
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
            let errMsg = 'Server error';
            try { errMsg = (await resp.json()).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
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
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${data.error || 'Could not read document. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('NRIC upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check connection and try again.</span>';
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
            let errMsg = 'Server error';
            try { errMsg = (await resp.json()).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
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
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${data.error || 'Could not read document. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('Property upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check connection and try again.</span>';
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
            let errMsg = 'Server error';
            try { errMsg = (await resp.json()).error || errMsg; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${errMsg}</span>`;
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
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">${data.error || 'Could not read document. Try a clearer image.'}</span>`;
        }
    } catch (e) {
        console.error('Asset upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Check connection and try again.</span>';
    }
}
