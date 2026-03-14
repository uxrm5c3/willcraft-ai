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

// ---- NRIC / Passport OCR Upload -------------------------------------------

async function uploadAndExtractNRIC(inputEl, statusElId, fieldMapping) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">Scanning document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/nric', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            const ext = data.extracted;
            if (fieldMapping.name && ext.full_name) setValueIfEmpty(fieldMapping.name, ext.full_name);
            if (fieldMapping.nric && ext.nric_number) setValueIfEmpty(fieldMapping.nric, ext.nric_number);
            if (fieldMapping.address && ext.address) setValueIfEmpty(fieldMapping.address, ext.address);
            if (fieldMapping.dob && ext.date_of_birth) {
                const dateVal = convertDateForInput(ext.date_of_birth);
                if (dateVal) setValueIfEmpty(fieldMapping.dob, dateVal);
            }
            if (fieldMapping.gender && ext.gender) {
                const genderField = document.querySelector(`[name="${fieldMapping.gender}"]`);
                if (genderField) { genderField.value = ext.gender; genderField.classList.add('bg-yellow-50'); }
            }
            if (fieldMapping.nationality && ext.nationality) setValueIfEmpty(fieldMapping.nationality, ext.nationality);
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">Data extracted successfully!</span>';
            setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed.</span>';
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
    // Convert DD-MM-YYYY to YYYY-MM-DD for <input type="date">
    if (!dateStr) return '';
    const parts = dateStr.split('-');
    if (parts.length === 3 && parts[0].length <= 2) {
        return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
    }
    return dateStr;
}

// ---- Property Document OCR Upload -----------------------------------------

async function uploadAndExtractProperty(inputEl, statusElId, giftIndex, docType) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">Scanning property document...</span>';

    const formData = new FormData();
    formData.append('file', file);
    if (docType) formData.append('doc_type', docType);

    try {
        const resp = await fetch('/api/ocr/property', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            const ext = data.extracted;
            // Fill structured property fields
            if (ext.property_address) setFieldValue(`gift_prop_address_${giftIndex}`, ext.property_address);
            if (ext.title_type) {
                const ttField = document.querySelector(`[name="gift_prop_title_type_${giftIndex}"]`);
                if (ttField) { ttField.value = ext.title_type; ttField.classList.add('bg-yellow-50'); }
            }
            if (ext.title_number) setFieldValue(`gift_prop_title_number_${giftIndex}`, ext.title_number);
            if (ext.lot_number) setFieldValue(`gift_prop_lot_number_${giftIndex}`, ext.lot_number);
            if (ext.bandar_pekan || ext.mukim) setFieldValue(`gift_prop_bandar_${giftIndex}`, ext.bandar_pekan || ext.mukim || '');
            if (ext.daerah) setFieldValue(`gift_prop_daerah_${giftIndex}`, ext.daerah);
            if (ext.negeri) {
                const ngField = document.querySelector(`[name="gift_prop_negeri_${giftIndex}"]`);
                if (ngField) { ngField.value = ext.negeri.toUpperCase(); ngField.classList.add('bg-yellow-50'); }
            }
            // Switch to property type and update preview
            const propRadio = document.querySelector(`[name="gift_type_${giftIndex}"][value="property"]`);
            if (propRadio) { propRadio.checked = true; switchGiftType(giftIndex, 'property'); }
            updatePropertyPreview(giftIndex);
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">Property data extracted!</span>';
            setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed.</span>';
    }
}

// ---- Asset Document OCR Upload --------------------------------------------

async function uploadAndExtractAsset(inputEl, statusElId, giftIndex) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">Scanning financial document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/asset', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            const ext = data.extracted;
            // Fill structured financial fields
            if (ext.assets && ext.assets.length > 0) {
                const a = ext.assets[0];
                if (a.institution) setFieldValue(`gift_fin_institution_${giftIndex}`, a.institution);
                if (a.account_number) setFieldValue(`gift_fin_account_${giftIndex}`, a.account_number);
                if (a.type) {
                    const typeField = document.querySelector(`[name="gift_fin_type_${giftIndex}"]`);
                    if (typeField) { typeField.value = a.type; typeField.classList.add('bg-yellow-50'); }
                }
                if (a.description) setFieldValue(`gift_fin_desc_${giftIndex}`, a.description);
            }
            // Switch to financial type
            const finRadio = document.querySelector(`[name="gift_type_${giftIndex}"][value="financial"]`);
            if (finRadio) { finRadio.checked = true; switchGiftType(giftIndex, 'financial'); }
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">Asset data extracted!</span>';
            setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed.</span>';
    }
}
