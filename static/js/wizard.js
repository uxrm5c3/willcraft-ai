/* WillCraft AI - Wizard JavaScript */

// Save Draft via AJAX
async function saveDraft() {
    const statusEl = document.getElementById('save-status');
    if (statusEl) statusEl.textContent = 'Saving...';
    try {
        const resp = await fetch('/api/will/save', { method: 'POST' });
        const data = await resp.json();
        if (statusEl) {
            statusEl.textContent = data.ok ? 'Saved!' : 'Error saving.';
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Save failed.';
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
// OCR CONFIRMATION MODAL SYSTEM
// ===========================================================================

let _ocrPendingData = null;       // Extracted data waiting for confirmation
let _ocrPendingCallback = null;   // Function to call with confirmed data
let _ocrRetryInputEl = null;      // File input to re-trigger for retry
let _ocrRetryArgs = null;         // Arguments for retry

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
 */
function showOCRConfirmation(extracted, imageFile, callback, retryInputEl, retryArgs) {
    _ocrPendingData = extracted;
    _ocrPendingCallback = callback;
    _ocrRetryInputEl = retryInputEl;
    _ocrRetryArgs = retryArgs;

    // Show image preview
    const previewImg = document.getElementById('ocr-preview-img');
    if (imageFile && imageFile.type.startsWith('image/')) {
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
    // Re-trigger the file input so user can pick a new file
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
// CAMERA CAPTURE HELPERS
// ===========================================================================

/**
 * Open a file picker that prefers camera capture on mobile devices.
 * @param {HTMLInputElement} hiddenInput - The hidden file input to trigger
 */
function openCameraCapture(hiddenInput) {
    hiddenInput.value = '';
    hiddenInput.click();
}

// ===========================================================================
// NRIC / Passport OCR Upload (with confirmation)
// ===========================================================================

async function uploadAndExtractNRIC(inputEl, statusElId, fieldMapping) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/nric', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">Review extracted data...</span>';

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
            }, inputEl, [inputEl, statusElId, fieldMapping]);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Please try again.</span>';
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

async function uploadAndExtractProperty(inputEl, statusElId, giftIndex, docType) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning property document...</span>';

    const formData = new FormData();
    formData.append('file', file);
    if (docType) formData.append('doc_type', docType);

    try {
        const resp = await fetch('/api/ocr/property', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">Review extracted data...</span>';

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
            }, inputEl, [inputEl, statusElId, giftIndex, docType]);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Please try again.</span>';
    }
}

// ===========================================================================
// Asset Document OCR Upload (with confirmation)
// ===========================================================================

async function uploadAndExtractAsset(inputEl, statusElId, giftIndex) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning financial document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/asset', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            if (statusEl) statusEl.innerHTML = '<span class="text-blue-600">Review extracted data...</span>';

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
            }, inputEl, [inputEl, statusElId, giftIndex]);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed. Please try again.</span>';
    }
}
