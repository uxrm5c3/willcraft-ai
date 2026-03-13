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

async function uploadAndExtractProperty(inputEl, statusElId, giftIndex) {
    const file = inputEl.files[0];
    if (!file) return;
    const statusEl = document.getElementById(statusElId);
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">Scanning property document...</span>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/ocr/property', { method: 'POST', body: formData });
        const data = await resp.json();
        if (data.ok && data.extracted) {
            const ext = data.extracted;
            // Build property description from extracted data
            let desc = '';
            if (ext.property_address) desc += ext.property_address;
            if (ext.title_type && ext.title_number) {
                desc += ` (${ext.title_type} ${ext.title_number}`;
                if (ext.lot_number) desc += `, Lot ${ext.lot_number}`;
                desc += ')';
            }
            if (ext.mukim) desc += `, Mukim ${ext.mukim}`;
            if (ext.daerah) desc += `, Daerah ${ext.daerah}`;
            if (ext.negeri) desc += `, ${ext.negeri}`;
            if (desc) {
                const descField = document.querySelector(`[name="gift_desc_${giftIndex}"]`);
                if (descField) { descField.value = desc; descField.classList.add('bg-yellow-50'); }
            }
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
            let desc = '';
            if (ext.assets && ext.assets.length > 0) {
                desc = ext.assets.map(a => {
                    let line = '';
                    if (a.institution) line += a.institution;
                    if (a.account_number) line += ` (Account: ${a.account_number})`;
                    if (a.type) line += ` - ${a.type}`;
                    if (a.description) line += `: ${a.description}`;
                    return line;
                }).join('\n');
            }
            if (desc) {
                const descField = document.querySelector(`[name="gift_desc_${giftIndex}"]`);
                if (descField) { descField.value = desc; descField.classList.add('bg-yellow-50'); }
            }
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">Asset data extracted!</span>';
            setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">Error: ${data.error || 'Extraction failed'}</span>`;
        }
    } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">Upload failed.</span>';
    }
}
