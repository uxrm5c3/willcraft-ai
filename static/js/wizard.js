/* WillCraft AI - Wizard JavaScript (v20260319b) */

// ===========================================================================
// Searchable Dropdown Component
// ===========================================================================

const RELATIONSHIP_LIST = [
    { value: 'Testator', label: 'Testator (Will Maker)', group: '' },
    { value: 'Spouse', label: 'Spouse', group: 'Immediate Family' },
    { value: 'Husband', label: 'Husband', group: 'Immediate Family' },
    { value: 'Wife', label: 'Wife', group: 'Immediate Family' },
    { value: 'Son', label: 'Son', group: 'Immediate Family' },
    { value: 'Daughter', label: 'Daughter', group: 'Immediate Family' },
    { value: 'Father', label: 'Father', group: 'Parents' },
    { value: 'Mother', label: 'Mother', group: 'Parents' },
    { value: 'Brother', label: 'Brother', group: 'Siblings' },
    { value: 'Sister', label: 'Sister', group: 'Siblings' },
    { value: 'Grandson', label: 'Grandson', group: 'Grandchildren & Grandparents' },
    { value: 'Granddaughter', label: 'Granddaughter', group: 'Grandchildren & Grandparents' },
    { value: 'Grandfather', label: 'Grandfather', group: 'Grandchildren & Grandparents' },
    { value: 'Grandmother', label: 'Grandmother', group: 'Grandchildren & Grandparents' },
    { value: 'Father-in-law', label: 'Father-in-law', group: 'In-Laws' },
    { value: 'Mother-in-law', label: 'Mother-in-law', group: 'In-Laws' },
    { value: 'Son-in-law', label: 'Son-in-law', group: 'In-Laws' },
    { value: 'Daughter-in-law', label: 'Daughter-in-law', group: 'In-Laws' },
    { value: 'Brother-in-law', label: 'Brother-in-law', group: 'In-Laws' },
    { value: 'Sister-in-law', label: 'Sister-in-law', group: 'In-Laws' },
    { value: 'Stepson', label: 'Stepson', group: 'Step/Adopted' },
    { value: 'Stepdaughter', label: 'Stepdaughter', group: 'Step/Adopted' },
    { value: 'Adopted Son', label: 'Adopted Son', group: 'Step/Adopted' },
    { value: 'Adopted Daughter', label: 'Adopted Daughter', group: 'Step/Adopted' },
    { value: 'Uncle', label: 'Uncle', group: 'Extended Family' },
    { value: 'Aunt', label: 'Aunt', group: 'Extended Family' },
    { value: 'Nephew', label: 'Nephew', group: 'Extended Family' },
    { value: 'Niece', label: 'Niece', group: 'Extended Family' },
    { value: 'Cousin', label: 'Cousin', group: 'Extended Family' },
    { value: 'Friend', label: 'Friend', group: 'Others' },
    { value: 'Business Partner', label: 'Business Partner', group: 'Others' },
    { value: 'Other', label: 'Other (specify below)', group: 'Others' },
];

const NATIONALITY_LIST = [
    'Malaysian', 'Singaporean', 'Indonesian', 'Thai', 'Filipino',
    'Vietnamese', 'Cambodian', 'Myanmar', 'Bruneian', 'Laotian',
    'Chinese', 'Indian', 'Japanese', 'Korean', 'Taiwanese',
    'Bangladeshi', 'Pakistani', 'Sri Lankan', 'Nepali',
    'British', 'American', 'Canadian', 'Australian', 'New Zealander',
    'German', 'French', 'Dutch', 'Italian', 'Spanish', 'Swiss',
    'Saudi Arabian', 'Emirati', 'Qatari', 'Kuwaiti',
    'South African', 'Nigerian', 'Egyptian',
    'Brazilian', 'Mexican', 'Argentine',
    'Russian', 'Turkish', 'Iranian',
];

const MALAYSIAN_STATES = [
    'Johor', 'Kedah', 'Kelantan', 'Melaka', 'Negeri Sembilan',
    'Pahang', 'Perak', 'Perlis', 'Pulau Pinang', 'Sabah',
    'Sarawak', 'Selangor', 'Terengganu',
    'Wilayah Persekutuan Kuala Lumpur', 'Wilayah Persekutuan Putrajaya', 'Wilayah Persekutuan Labuan',
];

const COUNTRY_LIST = [
    'Malaysia', 'Singapore', 'Indonesia', 'Thailand', 'Philippines',
    'Vietnam', 'Cambodia', 'Myanmar', 'Brunei', 'Laos',
    'China', 'India', 'Japan', 'South Korea', 'Taiwan',
    'Bangladesh', 'Pakistan', 'Sri Lanka', 'Nepal',
    'United Kingdom', 'United States', 'Canada', 'Australia', 'New Zealand',
    'Germany', 'France', 'Netherlands', 'Italy', 'Spain', 'Switzerland',
    'Saudi Arabia', 'United Arab Emirates', 'Qatar', 'Kuwait',
    'South Africa', 'Nigeria', 'Egypt',
    'Brazil', 'Mexico', 'Argentina',
    'Russia', 'Turkey', 'Iran',
];

// ===========================================================================
// Address Field Helpers (split ↔ combined)
// ===========================================================================

/**
 * Combine split address fields into a single address string for storage.
 * Format: "STREET, POSTCODE CITY, STATE"
 */
function combineAddressFields() {
    const street = (document.getElementById('modal-address-street')?.value || '').trim();
    const postcode = (document.getElementById('modal-address-postcode')?.value || '').trim();
    const city = (document.getElementById('modal-address-city')?.value || '').trim();
    const country = (document.getElementById('modal-address-country')?.value || 'Malaysia').trim();

    // State comes from dropdown (Malaysia) or free text (other countries)
    let state = '';
    if (country === 'Malaysia') {
        state = (document.getElementById('modal-address-state')?.value || '').trim();
    } else {
        state = (document.getElementById('modal-address-state-freetext')?.value || '').trim();
    }

    let parts = [];
    if (street) parts.push(street);
    let cityLine = '';
    if (postcode && city) cityLine = `${postcode} ${city}`;
    else if (postcode) cityLine = postcode;
    else if (city) cityLine = city;
    if (cityLine) parts.push(cityLine);
    if (state) parts.push(state);
    // Only include country if not Malaysia (default)
    if (country && country !== 'Malaysia') parts.push(country);

    const combined = parts.join(', ');
    const hidden = document.getElementById('modal-address');
    if (hidden) hidden.value = combined;
    return combined;
}

/**
 * Parse a combined address string into split fields.
 * Tries to extract postcode (5 digits), state, and remaining street.
 */
function splitAddressToFields(address) {
    if (!address) {
        ['modal-address-street', 'modal-address-postcode', 'modal-address-city', 'modal-address-state-freetext'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        _setSearchableValue('modal-address-state', 'modal-address-state-search', '', MALAYSIAN_STATES);
        _setSearchableValue('modal-address-country', 'modal-address-country-search', 'Malaysia', COUNTRY_LIST);
        toggleCountryState('Malaysia');
        return;
    }

    // Split by comma or newline
    const segments = address.split(/[,\n]+/).map(s => s.trim()).filter(Boolean);

    let street = '', postcode = '', city = '', state = '';

    // State aliases for fuzzy matching
    const stateAliases = {
        'WILAYAH PERSEKUTUAN': 'Wilayah Persekutuan Kuala Lumpur',
        'W.P.': 'Wilayah Persekutuan Kuala Lumpur',
        'WP KUALA LUMPUR': 'Wilayah Persekutuan Kuala Lumpur',
        'WP PUTRAJAYA': 'Wilayah Persekutuan Putrajaya',
        'WP LABUAN': 'Wilayah Persekutuan Labuan',
        'PULAU PINANG': 'Pulau Pinang',
        'PENANG': 'Pulau Pinang',
        'N. SEMBILAN': 'Negeri Sembilan',
        'N.SEMBILAN': 'Negeri Sembilan',
        'JOHOR BAHRU': 'Johor',
        'JB': 'Johor',
        'KL': 'Wilayah Persekutuan Kuala Lumpur',
    };

    // Try to find state (last segment matching a known state or alias)
    for (let i = segments.length - 1; i >= 0; i--) {
        const seg = segments[i].toUpperCase().trim();
        // Check aliases first
        const alias = Object.keys(stateAliases).find(a => seg.includes(a));
        if (alias) {
            state = stateAliases[alias];
            segments.splice(i, 1);
            break;
        }
        // Check exact state names
        const matchedState = MALAYSIAN_STATES.find(s => seg.includes(s.toUpperCase()));
        if (matchedState) {
            state = matchedState;
            segments.splice(i, 1);
            break;
        }
    }

    // Try to find postcode (5-digit number) in remaining segments
    for (let i = 0; i < segments.length; i++) {
        const match = segments[i].match(/\b(\d{5})\b/);
        if (match) {
            postcode = match[1];
            // The rest of this segment is the city
            const remainder = segments[i].replace(match[0], '').trim();
            if (remainder) city = remainder;
            segments.splice(i, 1);
            break;
        }
    }

    // Everything else is the street
    street = segments.join(', ');

    document.getElementById('modal-address-street').value = street;
    document.getElementById('modal-address-postcode').value = postcode;
    document.getElementById('modal-address-city').value = city;

    // Determine country from address (check last segment or default to Malaysia)
    let country = 'Malaysia';
    if (segments.length > 0) {
        // Check if any remaining segment looks like a country name
        const lastSeg = segments[segments.length - 1].trim();
        const matchedCountry = COUNTRY_LIST.find(c => c.toUpperCase() === lastSeg.toUpperCase());
        if (matchedCountry) {
            country = matchedCountry;
            segments.pop();
            // Recombine street without the country segment
            street = segments.join(', ');
            document.getElementById('modal-address-street').value = street;
        }
    }

    // Set country and toggle state field (don't combine yet — state not set)
    _setSearchableValue('modal-address-country', 'modal-address-country-search', country, COUNTRY_LIST);
    toggleCountryState(country, true); // skipCombine=true

    // Set state value based on country
    if (country === 'Malaysia') {
        _setSearchableValue('modal-address-state', 'modal-address-state-search', state, MALAYSIAN_STATES);
    } else {
        const freetext = document.getElementById('modal-address-state-freetext');
        if (freetext) freetext.value = state;
    }

    // Now combine with all fields set
    combineAddressFields();
}

/**
 * Toggle state field between searchable dropdown (Malaysia) and free text (other countries).
 */
function toggleCountryState(country, skipCombine) {
    const dropdownWrap = document.getElementById('state-dropdown-wrap');
    const freetext = document.getElementById('modal-address-state-freetext');
    if (!dropdownWrap || !freetext) return;

    if (country === 'Malaysia') {
        dropdownWrap.classList.remove('hidden');
        freetext.classList.add('hidden');
        freetext.value = '';
    } else {
        dropdownWrap.classList.add('hidden');
        freetext.classList.remove('hidden');
        // Clear the searchable state dropdown
        _setSearchableValue('modal-address-state', 'modal-address-state-search', '', MALAYSIAN_STATES);
    }
    if (!skipCombine) combineAddressFields();
}

/**
 * Initialize a searchable dropdown.
 * @param {string} searchInputId - ID of the text input for searching
 * @param {string} dropdownId - ID of the dropdown container div
 * @param {string} hiddenInputId - ID of the hidden input storing the selected value
 * @param {Array} items - Array of items: either strings or {value, label, group} objects
 * @param {object} opts - Options: { allowCustom: bool, onChange: fn }
 */
function initSearchableDropdown(searchInputId, dropdownId, hiddenInputId, items, opts = {}) {
    const searchInput = document.getElementById(searchInputId);
    const dropdown = document.getElementById(dropdownId);
    const hiddenInput = document.getElementById(hiddenInputId);
    if (!searchInput || !dropdown || !hiddenInput) return;

    let highlightIdx = -1;

    function renderDropdown(filter = '') {
        dropdown.innerHTML = '';
        const lf = filter.toLowerCase();
        let lastGroup = '';
        let idx = 0;
        let hasMatch = false;

        for (const item of items) {
            const val = typeof item === 'string' ? item : item.value;
            const label = typeof item === 'string' ? item : item.label;
            const group = typeof item === 'string' ? '' : (item.group || '');

            if (lf && !label.toLowerCase().includes(lf) && !val.toLowerCase().includes(lf)) continue;
            hasMatch = true;

            if (group && group !== lastGroup) {
                const groupEl = document.createElement('div');
                groupEl.className = 'px-3 py-1 text-xs font-semibold text-gray-400 bg-gray-50 sticky top-0';
                groupEl.textContent = group;
                dropdown.appendChild(groupEl);
                lastGroup = group;
            }

            const optEl = document.createElement('div');
            optEl.className = 'px-3 py-2 text-sm cursor-pointer hover:bg-primary-50 hover:text-primary-700 transition-colors';
            optEl.dataset.value = val;
            optEl.dataset.idx = idx;
            optEl.textContent = label;
            if (val === hiddenInput.value) {
                optEl.classList.add('bg-primary-50', 'text-primary-700', 'font-medium');
            }
            optEl.addEventListener('mousedown', function(e) {
                e.preventDefault(); // Prevent blur before click
                selectItem(val, label);
            });
            dropdown.appendChild(optEl);
            idx++;
        }

        if (!hasMatch && lf) {
            if (opts.allowCustom) {
                const customEl = document.createElement('div');
                customEl.className = 'px-3 py-2 text-sm text-gray-500 italic';
                customEl.textContent = `Use "${filter}" as custom value`;
                customEl.addEventListener('mousedown', function(e) {
                    e.preventDefault();
                    selectItem(filter, filter);
                });
                dropdown.appendChild(customEl);
            } else {
                const noEl = document.createElement('div');
                noEl.className = 'px-3 py-2 text-sm text-gray-400 italic';
                noEl.textContent = 'No matches found';
                dropdown.appendChild(noEl);
            }
        }

        highlightIdx = -1;
        dropdown.classList.remove('hidden');
    }

    function selectItem(value, label) {
        hiddenInput.value = value;
        searchInput.value = label.replace(' (specify below)', '');
        dropdown.classList.add('hidden');
        if (opts.onChange) opts.onChange(value);
    }

    function highlightItem(dir) {
        const optEls = dropdown.querySelectorAll('[data-idx]');
        if (optEls.length === 0) return;
        highlightIdx += dir;
        if (highlightIdx < 0) highlightIdx = optEls.length - 1;
        if (highlightIdx >= optEls.length) highlightIdx = 0;
        optEls.forEach((el, i) => {
            el.classList.toggle('bg-primary-100', i === highlightIdx);
        });
        optEls[highlightIdx].scrollIntoView({ block: 'nearest' });
    }

    searchInput.addEventListener('focus', function() {
        renderDropdown(this.value);
    });

    searchInput.addEventListener('input', function() {
        renderDropdown(this.value);
        if (opts.allowCustom) {
            hiddenInput.value = this.value;
        }
    });

    searchInput.addEventListener('blur', function() {
        setTimeout(() => dropdown.classList.add('hidden'), 150);
        // If allowCustom and user typed something, set it
        if (opts.allowCustom && this.value.trim()) {
            hiddenInput.value = this.value.trim();
        }
    });

    searchInput.addEventListener('keydown', function(e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); highlightItem(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); highlightItem(-1); }
        else if (e.key === 'Enter') {
            e.preventDefault();
            const optEls = dropdown.querySelectorAll('[data-idx]');
            if (highlightIdx >= 0 && highlightIdx < optEls.length) {
                const el = optEls[highlightIdx];
                selectItem(el.dataset.value, el.textContent);
            } else if (optEls.length === 1) {
                selectItem(optEls[0].dataset.value, optEls[0].textContent);
            } else if (opts.allowCustom && this.value.trim()) {
                selectItem(this.value.trim(), this.value.trim());
            }
        } else if (e.key === 'Escape') {
            dropdown.classList.add('hidden');
        }
    });

    // Public API for setting value programmatically
    searchInput._setSearchableValue = function(value) {
        hiddenInput.value = value;
        const match = items.find(i => (typeof i === 'string' ? i : i.value) === value);
        searchInput.value = match ? (typeof match === 'string' ? match : match.label.replace(' (specify below)', '')) : value;
    };
}

// Initialize searchable dropdowns when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    const relSearch = document.getElementById('modal-relationship-search');
    if (relSearch) {
        initSearchableDropdown('modal-relationship-search', 'modal-relationship-dropdown', 'modal-relationship',
            RELATIONSHIP_LIST, {
                allowCustom: false,
                onChange: function(val) { toggleRelationshipOther(); }
            });
    }
    const natSearch = document.getElementById('modal-nationality-search');
    if (natSearch) {
        initSearchableDropdown('modal-nationality-search', 'modal-nationality-dropdown', 'modal-nationality',
            NATIONALITY_LIST, { allowCustom: true });
    }
    // State searchable dropdown
    const stateSearch = document.getElementById('modal-address-state-search');
    if (stateSearch) {
        initSearchableDropdown('modal-address-state-search', 'modal-address-state-dropdown', 'modal-address-state',
            MALAYSIAN_STATES, { allowCustom: true, onChange: combineAddressFields });
    }
    // Country searchable dropdown
    const countrySearch = document.getElementById('modal-address-country-search');
    if (countrySearch) {
        initSearchableDropdown('modal-address-country-search', 'modal-address-country-dropdown', 'modal-address-country',
            COUNTRY_LIST, {
                allowCustom: true,
                onChange: function(val) {
                    toggleCountryState(val);
                }
            });
    }
    // State free text input (for non-Malaysia) — combine on input
    const stateFreetext = document.getElementById('modal-address-state-freetext');
    if (stateFreetext) stateFreetext.addEventListener('input', combineAddressFields);
    // Auto-combine address fields on change
    ['modal-address-street', 'modal-address-postcode', 'modal-address-city'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', combineAddressFields);
    });
});

// Apply dropdown filtering on initial page load + dedup change listeners
document.addEventListener('DOMContentLoaded', function() {
    if (document.querySelector('select.person-select[data-role]')) {
        refreshPersonDropdowns();
    }
    // When any person-select changes, refresh siblings to remove duplicates
    document.addEventListener('change', function(e) {
        if (e.target && e.target.classList.contains('person-select')) {
            refreshPersonDropdowns();
        }
    });
});

// Save Draft via AJAX
async function saveDraft() {
    const statusEl = document.getElementById('save-status');
    const statusMobile = document.getElementById('save-status-mobile');
    if (statusEl) statusEl.textContent = 'Saving...';
    if (statusMobile) statusMobile.textContent = 'Saving...';
    try {
        // First, submit current step's form data to save it to session
        const form = document.querySelector('form[method="POST"]');
        if (form) {
            const formData = new FormData(form);
            formData.append('_save_draft', '1'); // Flag to indicate draft save (no redirect)
            await fetch(form.action || window.location.href, {
                method: 'POST',
                body: formData,
            });
        }
        // Then save the session to DB
        const resp = await fetch('/api/will/save', { method: 'POST' });
        const data = await resp.json();
        const msg = data.ok ? 'Draft saved!' : 'Error saving.';
        if (statusEl) { statusEl.textContent = msg; setTimeout(() => { statusEl.textContent = ''; }, 3000); }
        if (statusMobile) { statusMobile.textContent = msg; setTimeout(() => { statusMobile.textContent = ''; }, 3000); }
        window._formDirty = false;
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Save failed.';
        if (statusMobile) statusMobile.textContent = 'Save failed.';
    }
}

// Track unsaved changes and auto-save before navigating away
window._formDirty = false;
document.addEventListener('DOMContentLoaded', function() {
    const form = document.querySelector('form[method="POST"]');
    if (form) {
        form.addEventListener('input', function() { window._formDirty = true; });
        form.addEventListener('change', function() { window._formDirty = true; });
        form.addEventListener('submit', function() { window._formDirty = false; });
    }

    // Auto-save form when clicking navigation links (sidebar, back, step links)
    document.addEventListener('click', function(e) {
        const link = e.target.closest('a[href*="/wizard/step/"], a[href="/preview"], a[href="/"]');
        if (!link || !window._formDirty || !form) return;
        e.preventDefault();
        const dest = link.href;
        // Submit form data via AJAX to save, then navigate
        const formData = new FormData(form);
        formData.append('_save_draft', '1');
        fetch(form.action || window.location.href, { method: 'POST', body: formData })
            .finally(() => { window._formDirty = false; window.location.href = dest; });
    });
});
window.addEventListener('beforeunload', function(e) {
    if (window._formDirty) {
        e.preventDefault();
        e.returnValue = '';
    }
});

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
    // Show photo guidance tip for NRIC
    if (docType === 'nric') {
        _showPhotoTip('📷 Photo Tips for IC/Passport', [
            'Hold the card upright in your hand (not flat on table)',
            'Use good, even lighting — avoid shadows and glare',
            'Make sure all text on the card is clearly visible',
            'Keep the card steady and in focus before taking photo'
        ], function() {
            _launchNativeFileInput(callback);
        });
        return;
    }
    _launchNativeFileInput(callback);
}

function _showPhotoTip(title, tips, onContinue) {
    const overlay = document.createElement('div');
    overlay.id = 'photo-tip-overlay';
    overlay.className = 'fixed inset-0 bg-black/60 z-[70] flex items-center justify-center p-4';
    overlay.innerHTML = `
        <div class="bg-white rounded-2xl max-w-sm w-full p-5 shadow-xl">
            <h3 class="text-lg font-bold text-gray-900 mb-3">${title}</h3>
            <ul class="space-y-2 mb-5">
                ${tips.map(t => `<li class="flex items-start gap-2 text-sm text-gray-700"><span class="text-green-500 mt-0.5">✓</span><span>${t}</span></li>`).join('')}
            </ul>
            <div class="flex gap-3">
                <button onclick="document.getElementById('photo-tip-overlay').remove()" class="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg text-gray-600 font-medium">Cancel</button>
                <button id="photo-tip-continue" class="flex-1 px-4 py-2.5 bg-primary-600 text-white rounded-lg font-semibold hover:bg-primary-700">Take Photo</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    document.getElementById('photo-tip-continue').addEventListener('click', function() {
        overlay.remove();
        onContinue();
    });
}

function _launchNativeFileInput(callback) {
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
        if (subtitleEl) subtitleEl.textContent = 'Hold card upright with good lighting — avoid shadows';
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

function showOCRConfirmation(extracted, imageFile, callback, retryInputEl, retryArgs, retryCameraInfo, existingData) {
    _ocrPendingData = extracted;
    _ocrPendingCallback = callback;
    _ocrRetryInputEl = retryInputEl;
    _ocrRetryCamera = retryCameraInfo || null;

    const existing = existingData || {};

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
        const ocrVal = (value||'').toString().trim();
        const existVal = (existing[key]||'').toString().trim();
        const hasConflict = existVal !== '' && ocrVal !== '' && existVal !== ocrVal;
        const isEmpty = !ocrVal;
        const isAddress = key.toLowerCase().includes('address');

        if (hasConflict) {
            // Show conflict UI with radio buttons — default "Keep original"
            const existDisplay = existVal.replace(/"/g,'&quot;');
            const ocrDisplay = ocrVal.replace(/"/g,'&quot;');
            container.innerHTML += `<div class="flex flex-col gap-1 p-3 border border-amber-300 bg-amber-50 rounded-lg">
                <label class="text-sm font-semibold text-amber-800">${label} — different from existing</label>
                <label class="flex items-start gap-2 cursor-pointer text-sm p-1.5 rounded hover:bg-amber-100">
                    <input type="radio" name="ocr-choice-${key}" value="keep" checked
                           class="mt-0.5 text-primary-600 focus:ring-primary-500">
                    <span><span class="font-medium text-gray-700">Keep original:</span>
                    <span class="text-gray-900">${isAddress ? existDisplay.replace(/\n/g, ', ') : existDisplay}</span></span>
                </label>
                <label class="flex items-start gap-2 cursor-pointer text-sm p-1.5 rounded hover:bg-amber-100">
                    <input type="radio" name="ocr-choice-${key}" value="scanned"
                           class="mt-0.5 text-primary-600 focus:ring-primary-500">
                    <span><span class="font-medium text-gray-700">Use scanned:</span>
                    <span class="text-gray-900">${isAddress ? ocrDisplay.replace(/\n/g, ', ') : ocrDisplay}</span></span>
                </label>
                <input type="hidden" name="ocr-field-${key}" value="${existDisplay}">
                <input type="hidden" name="ocr-existing-${key}" value="${existDisplay}">
                <input type="hidden" name="ocr-scanned-${key}" value="${ocrDisplay}">
            </div>`;
        } else if (isAddress) {
            const bc = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
            let textareaValue = (value||'').toString();
            container.innerHTML += `<div class="flex flex-col gap-1">
                <label class="text-sm font-medium text-gray-700">${label}</label>
                <textarea name="ocr-field-${key}" rows="4"
                       class="w-full px-3 py-1.5 border ${bc} rounded-lg text-sm focus:ring-2 focus:ring-primary-500">${textareaValue}</textarea>
            </div>`;
        } else {
            const bc = isEmpty ? 'border-amber-300 bg-amber-50' : 'border-gray-300';
            const displayValue = (value||'').toString().replace(/"/g,'&quot;');
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
    const container = document.getElementById('ocr-fields-container');
    const data = {};
    const assetData = {};

    // Handle conflict fields — check which radio the user selected
    const choiceRadios = container.querySelectorAll('input[type="radio"][name^="ocr-choice-"]:checked');
    choiceRadios.forEach(r => {
        const key = r.name.replace('ocr-choice-', '');
        if (r.value === 'keep') {
            data[key] = container.querySelector(`[name="ocr-existing-${key}"]`).value;
        } else {
            data[key] = container.querySelector(`[name="ocr-scanned-${key}"]`).value;
        }
    });

    // Handle normal (non-conflict) fields
    const fields = container.querySelectorAll('input[name^="ocr-field-"], textarea[name^="ocr-field-"]');
    fields.forEach(f => {
        if (f.type === 'hidden') return; // skip hidden inputs used by conflict UI
        if (f.name.startsWith('ocr-field-asset_')) {
            assetData[f.name.replace('ocr-field-asset_', '')] = f.value;
        } else if (f.name.startsWith('ocr-field-')) {
            const key = f.name.replace('ocr-field-', '');
            if (!(key in data)) { // don't overwrite conflict-resolved values
                data[key] = f.value;
            }
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

            // Collect existing form values to detect conflicts
            const existingData = {};
            const _gv = (name) => { const el = document.querySelector(`[name="${name}"]`); return el ? el.value.trim() : ''; };
            if (fieldMapping.name) existingData.full_name = _gv(fieldMapping.name);
            if (fieldMapping.nric) existingData.nric_number = _gv(fieldMapping.nric);
            if (fieldMapping.address) existingData.address = _gv(fieldMapping.address);
            if (fieldMapping.dob) {
                const dobVal = _gv(fieldMapping.dob);
                // Convert YYYY-MM-DD back to DD-MM-YYYY for comparison
                if (dobVal && dobVal.includes('-') && dobVal.split('-')[0].length === 4) {
                    const p = dobVal.split('-');
                    existingData.date_of_birth = `${p[2]}-${p[1]}-${p[0]}`;
                } else {
                    existingData.date_of_birth = dobVal;
                }
            }
            if (fieldMapping.gender) existingData.gender = _gv(fieldMapping.gender);
            if (fieldMapping.nationality) existingData.nationality = _gv(fieldMapping.nationality);

            showOCRConfirmation(data.extracted, file, (confirmed) => {
                // User already chose keep/replace in modal — apply values directly
                const _setField = (name, val) => {
                    const el = document.querySelector(`[name="${name}"]`);
                    if (el && val) { el.value = val; el.classList.add('bg-yellow-50'); }
                };
                if (fieldMapping.name && confirmed.full_name) _setField(fieldMapping.name, confirmed.full_name);
                if (fieldMapping.nric && confirmed.nric_number) _setField(fieldMapping.nric, confirmed.nric_number);
                if (fieldMapping.address && confirmed.address) _setField(fieldMapping.address, confirmed.address);
                if (fieldMapping.dob && confirmed.date_of_birth) {
                    const d = convertDateForInput(confirmed.date_of_birth);
                    if (d) _setField(fieldMapping.dob, d);
                }
                if (fieldMapping.gender && confirmed.gender) {
                    const g = document.querySelector(`[name="${fieldMapping.gender}"]`);
                    if (g) { g.value = confirmed.gender; g.classList.add('bg-yellow-50'); }
                }
                if (fieldMapping.nationality && confirmed.nationality) _setField(fieldMapping.nationality, confirmed.nationality);
                if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Data applied!</span>';
                setTimeout(() => { if (statusEl) statusEl.innerHTML = ''; }, 5000);
            }, (inputOrFile instanceof HTMLElement) ? inputOrFile : null, null,
            { callback: (f) => uploadAndExtractNRIC(f, statusElId, fieldMapping), docType: 'nric' },
            existingData);
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
        } else if (data.ok && data.warning) {
            // OCR failed but file was saved
            if (statusEl) statusEl.innerHTML = `<span class="text-amber-600">⚠ ${data.warning}</span>`;
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the property document. Please try a clearer image.</span>';
        }
        // Add document to preview list regardless of OCR success
        if (data && data.ok) {
            _addGiftDocPreview(giftIndex, file.name, data.document_url || '', docType || 'document');
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

/**
 * Set a searchable dropdown's value programmatically.
 * Updates both the hidden input and the visible search text.
 */
function _setSearchableValue(hiddenId, searchId, value, items) {
    const hidden = document.getElementById(hiddenId);
    const search = document.getElementById(searchId);
    if (hidden) hidden.value = value || '';
    if (search) {
        if (!value) {
            search.value = '';
        } else {
            const match = items.find(i => (typeof i === 'string' ? i : i.value) === value);
            search.value = match
                ? (typeof match === 'string' ? match : match.label.replace(' (specify below)', ''))
                : value;
        }
    }
}

/**
 * Update ID type UI based on nationality and ID type radio selection.
 * Malaysian: hide ID type row, show "NRIC No." label, hide passport expiry.
 * Non-Malaysian: show ID type row with "Identification No." (default) or "Passport".
 *   - Identification No: hide passport expiry
 *   - Passport: show passport expiry
 */
function updateIdTypeUI() {
    const nationality = (document.getElementById('modal-nationality').value || '').trim();
    const isMalaysian = !nationality || nationality.toLowerCase() === 'malaysian';
    const idTypeRow = document.getElementById('modal-id-type-row');
    const label = document.getElementById('modal-nric-label');
    const expiryField = document.getElementById('passport-expiry-field');

    if (isMalaysian) {
        idTypeRow.classList.add('hidden');
        label.innerHTML = 'NRIC No. <span class="text-red-500">*</span>';
        expiryField.classList.add('hidden');
    } else {
        idTypeRow.classList.remove('hidden');
        const selectedType = document.querySelector('input[name="modal-id-type"]:checked');
        const isPassport = selectedType && selectedType.value === 'passport';
        if (isPassport) {
            label.innerHTML = 'Passport No. <span class="text-red-500">*</span>';
            expiryField.classList.remove('hidden');
        } else {
            label.innerHTML = 'Identification No. <span class="text-red-500">*</span>';
            expiryField.classList.add('hidden');
        }
    }
}

// Watch for nationality changes via MutationObserver on the hidden input
document.addEventListener('DOMContentLoaded', function() {
    const natInput = document.getElementById('modal-nationality');
    if (natInput) {
        // Use MutationObserver since hidden input value changes don't fire events
        const observer = new MutationObserver(function() { updateIdTypeUI(); });
        observer.observe(natInput, { attributes: true, attributeFilter: ['value'] });
        // Also listen for programmatic value changes via a polling check
        let lastNat = natInput.value;
        setInterval(function() {
            if (natInput.value !== lastNat) {
                lastNat = natInput.value;
                updateIdTypeUI();
            }
        }, 300);
    }
});

function openAddIdentityModal(presetRelationship) {
    const modal = document.getElementById('identity-modal');
    if (!modal) return;
    document.getElementById('modal-title').textContent = 'Add Identity';
    document.getElementById('modal-person-id').value = '';
    document.getElementById('modal-full-name').value = '';
    document.getElementById('modal-nric-passport').value = '';
    _setSearchableValue('modal-nationality', 'modal-nationality-search', 'Malaysian', NATIONALITY_LIST);
    // Reset ID type to default
    const idRadio = document.querySelector('input[name="modal-id-type"][value="id"]');
    if (idRadio) idRadio.checked = true;
    updateIdTypeUI();
    document.getElementById('modal-address').value = '';
    splitAddressToFields('');
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
    const relOther = document.getElementById('modal-relationship-other');
    if (presetRelationship) {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', presetRelationship, RELATIONSHIP_LIST);
    } else if (window._personRegistry && window._personRegistry.length === 0) {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', 'Testator', RELATIONSHIP_LIST);
    } else {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', '', RELATIONSHIP_LIST);
    }
    toggleRelationshipOther();
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
    _setSearchableValue('modal-nationality', 'modal-nationality-search', person.nationality || 'Malaysian', NATIONALITY_LIST);
    document.getElementById('modal-address').value = person.address || '';
    splitAddressToFields(person.address || '');
    document.getElementById('modal-dob').value = person.date_of_birth ? convertDateForInput(person.date_of_birth) : '';
    document.getElementById('modal-gender').value = person.gender || '';
    document.getElementById('modal-email').value = person.email || '';
    document.getElementById('modal-phone').value = person.phone || '';
    // ID type and passport expiry
    const isMalaysian = !person.nationality || person.nationality.toLowerCase() === 'malaysian';
    if (!isMalaysian && person.passport_expiry) {
        // Has passport expiry → select Passport radio
        const passRadio = document.querySelector('input[name="modal-id-type"][value="passport"]');
        if (passRadio) passRadio.checked = true;
        document.getElementById('modal-passport-expiry').value = convertDateForInput(person.passport_expiry);
    } else {
        // Default to Identification No
        const idRadio = document.querySelector('input[name="modal-id-type"][value="id"]');
        if (idRadio) idRadio.checked = true;
        document.getElementById('modal-passport-expiry').value = '';
    }
    updateIdTypeUI();
    // Relationship
    const relOther = document.getElementById('modal-relationship-other');
    const rel = person.relationship || '';
    if (RELATIONSHIP_OPTIONS.includes(rel)) {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', rel, RELATIONSHIP_LIST);
    } else if (rel) {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', 'Other', RELATIONSHIP_LIST);
        if (relOther) relOther.value = rel;
    } else {
        _setSearchableValue('modal-relationship', 'modal-relationship-search', '', RELATIONSHIP_LIST);
    }
    toggleRelationshipOther();
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
        btn.className = 'w-full text-left px-3 py-2 bg-white border border-blue-200 rounded-lg hover:bg-blue-100 hover:border-blue-400 transition-colors text-xs cursor-pointer';
        btn.innerHTML = `<span class="font-semibold text-blue-700">${label}</span> <span class="text-blue-500">— ${displayAddr}</span>`;
        btn.onclick = function() {
            document.getElementById('modal-address').value = a.address;
            splitAddressToFields(a.address);
            // Highlight the street field briefly
            const streetField = document.getElementById('modal-address-street');
            if (streetField) {
                streetField.classList.add('bg-green-50', 'border-green-400');
                setTimeout(() => { streetField.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
            }
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

/**
 * Validate Malaysian NRIC format and date coherence.
 * Returns { valid: true } or { valid: false, field: string, message: string }.
 */
function validateNRICAndDOB(nricPassport, dobValue) {
    const cleaned = nricPassport.replace(/[-\s]/g, '');

    // Check if it looks like a Malaysian IC (all digits, 12 chars)
    const isMalaysianIC = /^\d{12}$/.test(cleaned);

    if (isMalaysianIC) {
        // Validate format: first 6 digits must be valid YYMMDD
        const yy = parseInt(cleaned.substring(0, 2));
        const mm = parseInt(cleaned.substring(2, 4));
        const dd = parseInt(cleaned.substring(4, 6));

        if (mm < 1 || mm > 12) {
            return { valid: false, field: 'nric', message: `Invalid IC: month "${cleaned.substring(2, 4)}" is not valid (01-12).` };
        }

        // Determine full year
        const currentYY = new Date().getFullYear() % 100;
        const century = yy > (currentYY + 5) ? 1900 : 2000;
        const fullYear = century + yy;

        // Check day is valid for the given month/year
        const maxDay = new Date(fullYear, mm, 0).getDate(); // last day of month
        if (dd < 1 || dd > maxDay) {
            return { valid: false, field: 'nric', message: `Invalid IC: day "${cleaned.substring(4, 6)}" is not valid for month ${mm} (max ${maxDay}).` };
        }

        // Format check: accept YYMMDD-SS-NNNN or YYMMDDSSNNNN
        const formatted = nricPassport.replace(/[-\s]/g, '');
        if (formatted.length !== 12) {
            return { valid: false, field: 'nric', message: 'Malaysian IC must be 12 digits (YYMMDD-SS-NNNN).' };
        }

        // If DOB is provided, check it matches the IC
        if (dobValue) {
            // dobValue is in YYYY-MM-DD format (from date input)
            const dobParts = dobValue.split('-');
            if (dobParts.length === 3) {
                const dobYear = parseInt(dobParts[0]);
                const dobMonth = parseInt(dobParts[1]);
                const dobDay = parseInt(dobParts[2]);

                if (dobYear !== fullYear || dobMonth !== mm || dobDay !== dd) {
                    const icDate = `${String(dd).padStart(2, '0')}/${String(mm).padStart(2, '0')}/${fullYear}`;
                    const dobDate = `${dobParts[2]}/${dobParts[1]}/${dobParts[0]}`;
                    return { valid: false, field: 'dob', message: `Date of birth (${dobDate}) does not match IC number (${icDate}).` };
                }
            }
        }
    }

    // Validate DOB is a real date if provided
    if (dobValue) {
        const d = new Date(dobValue);
        if (isNaN(d.getTime())) {
            return { valid: false, field: 'dob', message: 'Date of birth is not a valid date.' };
        }
        if (d > new Date()) {
            return { valid: false, field: 'dob', message: 'Date of birth cannot be in the future.' };
        }
    }

    return { valid: true };
}

function showFieldError(fieldId, message) {
    clearFieldError(fieldId);
    const field = document.getElementById(fieldId);
    if (!field) return;
    field.classList.add('border-red-500', 'bg-red-50');
    const errEl = document.createElement('p');
    errEl.className = 'text-red-600 text-xs mt-1 field-error';
    errEl.setAttribute('data-error-for', fieldId);
    errEl.textContent = message;
    field.parentElement.appendChild(errEl);
}

function clearFieldError(fieldId) {
    const field = document.getElementById(fieldId);
    if (field) {
        field.classList.remove('border-red-500', 'bg-red-50');
    }
    const existing = document.querySelector(`[data-error-for="${fieldId}"]`);
    if (existing) existing.remove();
}

function clearAllFieldErrors() {
    document.querySelectorAll('.field-error').forEach(el => el.remove());
    document.querySelectorAll('.border-red-500').forEach(el => {
        el.classList.remove('border-red-500', 'bg-red-50');
    });
}

async function saveIdentityGlobal() {
    clearAllFieldErrors();
    const personId = document.getElementById('modal-person-id').value;
    const fullName = document.getElementById('modal-full-name').value.trim();
    const nricPassport = document.getElementById('modal-nric-passport').value.trim();
    if (!fullName || !nricPassport) {
        if (!fullName) showFieldError('modal-full-name', 'Name is required.');
        if (!nricPassport) showFieldError('modal-nric-passport', 'NRIC/Passport is required.');
        return;
    }

    // Validate email format
    const emailVal = document.getElementById('modal-email').value.trim();
    if (emailVal && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailVal)) {
        showFieldError('modal-email', 'Invalid email format (e.g. name@example.com).');
        document.getElementById('modal-email').scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
    }

    // Validate NRIC and DOB
    const dobValue = document.getElementById('modal-dob').value;
    const validation = validateNRICAndDOB(nricPassport, dobValue);
    if (!validation.valid) {
        const errorFieldId = validation.field === 'nric' ? 'modal-nric-passport' : 'modal-dob';
        showFieldError(errorFieldId, validation.message);
        document.getElementById(errorFieldId).scrollIntoView({ behavior: 'smooth', block: 'center' });
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

    // Only save passport expiry if non-Malaysian + Passport type selected
    let expiry = '';
    const idTypeSelected = document.querySelector('input[name="modal-id-type"]:checked');
    const isPassportType = idTypeSelected && idTypeSelected.value === 'passport';
    const natVal = (document.getElementById('modal-nationality').value || '').trim().toLowerCase();
    const isMalaysianNat = !natVal || natVal === 'malaysian';
    if (!isMalaysianNat && isPassportType) {
        const expiryRaw = document.getElementById('modal-passport-expiry').value;
        if (expiryRaw) {
            const parts = expiryRaw.split('-');
            if (parts.length === 3 && parts[0].length === 4) expiry = `${parts[2]}-${parts[1]}-${parts[0]}`;
            else expiry = expiryRaw;
        }
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

    // Combine split address fields into single value
    combineAddressFields();

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
        console.error('Save identity error:', e);
        alert('Failed to save identity. ' + (e.message || 'Check your internet connection and try again.'));
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
 * Auto-populate Date of Birth and Gender from Malaysian NRIC number.
 * Malaysian NRIC format: YYMMDD-SS-NNNN
 * - First 6 digits = date of birth (YYMMDD)
 * - Last digit: odd = Male, even = Female
 */
function autoPopulateFromNRIC(nricValue) {
    const nric = nricValue.replace(/[-\s]/g, '');
    if (!/^\d{12}$/.test(nric)) return; // Not a valid Malaysian NRIC

    const dobField = document.getElementById('modal-dob');
    const genderField = document.getElementById('modal-gender');

    // Extract date of birth
    const yy = parseInt(nric.substring(0, 2));
    const mm = nric.substring(2, 4);
    const dd = nric.substring(4, 6);
    const month = parseInt(mm);
    const day = parseInt(dd);

    // Validate month and day
    if (month < 1 || month > 12 || day < 1 || day > 31) return;

    // Determine century: if YY > current year's last 2 digits + 5, assume 1900s
    const currentYY = new Date().getFullYear() % 100;
    const century = yy > (currentYY + 5) ? '19' : '20';
    const fullYear = century + nric.substring(0, 2);

    // Only auto-fill DOB if the field is empty (don't overwrite user/OCR data)
    if (dobField && !dobField.value) {
        dobField.value = `${fullYear}-${mm}-${dd}`;
        dobField.classList.add('bg-green-50', 'border-green-400');
        setTimeout(() => { dobField.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
    }

    // Auto-fill gender from last digit: odd = Male, even = Female
    const lastDigit = parseInt(nric.charAt(11));
    if (genderField && !genderField.value) {
        genderField.value = (lastDigit % 2 === 1) ? 'Male' : 'Female';
        genderField.classList.add('bg-green-50', 'border-green-400');
        setTimeout(() => { genderField.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
    }
}

// Attach NRIC auto-populate and format listener
document.addEventListener('DOMContentLoaded', function() {
    const nricField = document.getElementById('modal-nric-passport');
    if (nricField) {
        nricField.addEventListener('input', function() {
            clearFieldError('modal-nric-passport');
            autoPopulateFromNRIC(this.value);
        });
        // Auto-format on blur: add dashes to Malaysian IC (YYMMDD-SS-NNNN)
        nricField.addEventListener('blur', function() {
            const cleaned = this.value.replace(/[-\s]/g, '');
            if (/^\d{12}$/.test(cleaned)) {
                this.value = cleaned.substring(0, 6) + '-' + cleaned.substring(6, 8) + '-' + cleaned.substring(8);
                // Validate on blur
                const dobValue = document.getElementById('modal-dob').value;
                const result = validateNRICAndDOB(this.value, dobValue);
                if (!result.valid && result.field === 'nric') {
                    showFieldError('modal-nric-passport', result.message);
                }
            }
        });
    }
    // Validate DOB on change
    const dobField = document.getElementById('modal-dob');
    if (dobField) {
        dobField.addEventListener('change', function() {
            clearFieldError('modal-dob');
            const nricVal = document.getElementById('modal-nric-passport').value.trim();
            if (nricVal && this.value) {
                const result = validateNRICAndDOB(nricVal, this.value);
                if (!result.valid && result.field === 'dob') {
                    showFieldError('modal-dob', result.message);
                }
            }
        });
    }
    // Validate email on blur
    const emailField = document.getElementById('modal-email');
    if (emailField) {
        emailField.addEventListener('blur', function() {
            clearFieldError('modal-email');
            const v = this.value.trim();
            if (v && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
                showFieldError('modal-email', 'Invalid email format (e.g. name@example.com).');
            }
        });
    }
});

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
 * @param {Set} excludeIds - Set of person IDs to exclude (already selected siblings)
 * @returns {string} HTML string of <option> elements
 */
function buildFilteredPersonOptions(role, excludeIds) {
    const testatorId = getTestatorId();
    const exclude = excludeIds || _getSelectedIdsForRole(role);
    let html = '<option value="">-- Select an identity --</option>';
    for (const p of window._personRegistry) {
        if (!_personPassesFilter(p, role, testatorId)) continue;
        if (exclude.has(p.id)) continue;
        html += `<option value="${p.id}" data-name="${p.full_name}" data-nric="${p.nric_passport}" data-address="${p.address || ''}" data-nationality="${p.nationality || 'Malaysian'}" data-dob="${p.date_of_birth || ''}" data-gender="${p.gender || ''}" data-email="${p.email || ''}" data-phone="${p.phone || ''}" data-relationship="${p.relationship || ''}">${p.full_name} (${p.nric_passport})${p.relationship ? ' [' + p.relationship + ']' : ''}</option>`;
    }
    return html;
}

/**
 * Get set of already-selected person IDs for a given role.
 */
function _getSelectedIdsForRole(role) {
    const ids = new Set();
    document.querySelectorAll(`select.person-select[data-role="${role}"]`).forEach(sel => {
        if (sel.value) ids.add(sel.value);
    });
    return ids;
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
 * Prevents duplicate selections within the same role group.
 */
function refreshPersonDropdowns() {
    const testatorId = getTestatorId();

    // Group dropdowns by role for dedup
    const roleGroups = {};
    document.querySelectorAll('select.person-select').forEach(sel => {
        const role = sel.dataset.role || '_default';
        if (!roleGroups[role]) roleGroups[role] = [];
        roleGroups[role].push(sel);
    });

    for (const [role, selects] of Object.entries(roleGroups)) {
        // Collect all selected values in this role group
        const allSelectedInGroup = new Set();
        selects.forEach(sel => {
            if (sel.value) allSelectedInGroup.add(sel.value);
        });

        selects.forEach(sel => {
            const currentVal = sel.value;

            // IDs selected by OTHER dropdowns in same group (exclude own value)
            const othersSelected = new Set(allSelectedInGroup);
            if (currentVal) othersSelected.delete(currentVal);

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
                // Apply role-based filters (age, testator exclusion)
                if (role !== '_default' && !_personPassesFilter(p, role, testatorId)) continue;
                // Apply dedup filter (skip persons selected by siblings)
                if (othersSelected.has(p.id)) continue;

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

/**
 * Show a per-field conflict resolution dialog when OCR data differs from existing form data.
 * Default is "maintain" (keep existing manual data).
 */
/**
 * Highlight character-level differences between two strings.
 * Differing characters in `str` (compared to `ref`) are wrapped in red italic spans.
 */
function _highlightDiff(str, ref) {
    let result = '';
    const maxLen = Math.max(str.length, ref.length);
    for (let i = 0; i < str.length; i++) {
        if (i >= ref.length || str[i] !== ref[i]) {
            result += `<span class="text-red-600 font-bold italic">${str[i]}</span>`;
        } else {
            result += str[i];
        }
    }
    return result;
}

function showFieldConflictDialog(conflicts, callback) {
    // Build modal overlay
    const overlay = document.createElement('div');
    overlay.id = 'field-conflict-modal';
    overlay.className = 'fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4';
    let rows = '';
    for (const c of conflicts) {
        const existingRaw = c.label === 'Address'
            ? c.existingVal.replace(/\n/g, ', ').substring(0, 60) + (c.existingVal.length > 60 ? '...' : '')
            : c.existingVal;
        const scannedRaw = c.label === 'Address'
            ? c.scannedVal.replace(/\n/g, ', ').substring(0, 60) + (c.scannedVal.length > 60 ? '...' : '')
            : c.scannedVal;
        // Highlight differing characters in scanned value
        const scannedHighlighted = _highlightDiff(scannedRaw, existingRaw);
        rows += `
        <div class="border border-gray-200 rounded-lg p-3 space-y-1">
            <div class="text-sm font-semibold text-gray-700 mb-2">Replace existing ${c.label}?</div>
            <div class="text-sm text-gray-600 mb-2">Existing: <strong>${existingRaw}</strong></div>
            <div class="text-sm text-gray-600 mb-3">Scanned: <strong>${scannedHighlighted}</strong></div>
            <label class="flex items-center gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50 border border-transparent has-[:checked]:border-primary-300 has-[:checked]:bg-primary-50">
                <input type="radio" name="conflict-${c.key}" value="existing" checked class="text-primary-600 focus:ring-primary-500">
                <span class="text-sm font-medium text-gray-700">Keep existing</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50 border border-transparent has-[:checked]:border-primary-300 has-[:checked]:bg-primary-50">
                <input type="radio" name="conflict-${c.key}" value="scanned" class="text-primary-600 focus:ring-primary-500">
                <span class="text-sm font-medium text-gray-700">Update info</span>
            </label>
        </div>`;
    }
    overlay.innerHTML = `
    <div class="bg-white rounded-xl shadow-xl max-w-md w-full max-h-[80vh] overflow-y-auto p-5">
        <h3 class="text-lg font-bold text-gray-900 mb-1">Existing Data Found</h3>
        <p class="text-sm text-gray-500 mb-4">Scanned data differs from existing. Differences are highlighted in <span class="text-red-600 font-bold italic">red</span>. Existing data is kept by default — OCR may be inaccurate.</p>
        <div class="space-y-3 mb-5">${rows}</div>
        <div class="flex justify-end gap-3 pt-3 border-t border-gray-200">
            <button type="button" id="conflict-apply-btn" class="px-5 py-2 bg-primary-600 text-white font-semibold rounded-lg hover:bg-primary-700 text-sm">Apply</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);

    document.getElementById('conflict-apply-btn').onclick = function() {
        const resolutions = {};
        for (const c of conflicts) {
            const radio = document.querySelector(`input[name="conflict-${c.key}"]:checked`);
            resolutions[c.key] = radio ? radio.value : 'existing';
        }
        overlay.remove();
        callback(resolutions);
    };
}

/**
 * Common post-OCR actions: show document preview, check duplicates, show banners.
 */
function _finishOCRApply(data, file, confirmed, statusEl) {
    if (data.document_id) {
        showDocumentPreview(data.document_id, file.name || 'IC/Passport scan', file);
    }
    if (confirmed.nric_number) {
        checkAndHandleDuplicate(confirmed.nric_number);
    }
    showUnsavedBanner();
    showAddressSuggestions();
    if (statusEl) statusEl.innerHTML = '';
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
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Uploading document...</span>';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('category', 'nric');

    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!resp.ok) {
            let errMsg = 'Upload failed. Please try again.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            return;
        }
        const data = await resp.json();
        if (data.ok && data.document_id) {
            showDocumentPreview(data.document_id, file.name || 'IC/Passport', file);
            if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ Document saved. Click <strong>🔍 Scan</strong> to extract data.</span>';
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Please try again.</span>';
        }
    } catch (e) {
        console.error('NRIC upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}

/**
 * Step 2: Scan an already-uploaded document with OCR and extract data.
 * Called from the "🔍 Scan" button in the document preview area.
 */
async function scanDocumentOCR() {
    const documentId = document.getElementById('modal-document-id').value;
    if (!documentId) {
        const statusEl = document.getElementById('modal-nric-status');
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">No document uploaded yet.</span>';
        return;
    }

    const statusEl = document.getElementById('modal-nric-status');
    if (statusEl) statusEl.innerHTML = '<span class="text-primary-600">⏳ Scanning document...</span>';

    // Disable scan button during processing
    const scanBtn = document.getElementById('btn-scan-ocr');
    if (scanBtn) { scanBtn.disabled = true; scanBtn.classList.add('opacity-50'); }

    try {
        const resp = await fetch(`/api/ocr/nric/${documentId}`, { method: 'POST' });
        if (!resp.ok) {
            let errMsg = 'Could not scan the document. Please try again.';
            try { const errData = await resp.json(); if (errData.error) errMsg = errData.error; } catch(e) {}
            if (statusEl) statusEl.innerHTML = `<span class="text-red-600">❌ ${errMsg}</span>`;
            if (scanBtn) { scanBtn.disabled = false; scanBtn.classList.remove('opacity-50'); }
            return;
        }
        const data = await resp.json();
        if (data.ok && data.extracted) {
            const extracted = data.extracted;
            const isPassport = (extracted.doc_type || '').toLowerCase() === 'passport';
            if (!isPassport) {
                delete extracted.passport_expiry;
            }
            delete extracted.doc_type;

            // Map OCR fields to modal form fields
            const fieldMap = [
                { key: 'full_name', elId: 'modal-full-name', label: 'Full Name' },
                { key: 'nric_number', elId: 'modal-nric-passport', label: 'NRIC / Passport' },
                { key: 'address', elId: 'modal-address', label: 'Address' },
                { key: 'nationality', elId: 'modal-nationality', label: 'Nationality' },
                { key: 'gender', elId: 'modal-gender', label: 'Gender' },
                { key: 'date_of_birth', elId: 'modal-dob', label: 'Date of Birth', isDate: true },
            ];
            if (isPassport) {
                fieldMap.push({ key: 'passport_expiry', elId: 'modal-passport-expiry', label: 'Passport Expiry', isDate: true });
            }

            // Compare each field: collect conflicts and auto-fill empty fields
            const conflicts = [];
            function _applyFieldValue(f, scannedVal) {
                const el = document.getElementById(f.elId);
                if (!el) return;
                // For select/dropdown elements, set both value and selectedIndex
                if (el.tagName === 'SELECT') {
                    for (let i = 0; i < el.options.length; i++) {
                        if (el.options[i].value === scannedVal || el.options[i].text === scannedVal) {
                            el.selectedIndex = i;
                            break;
                        }
                    }
                } else {
                    el.value = scannedVal;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                if (f.key === 'nationality') {
                    _setSearchableValue('modal-nationality', 'modal-nationality-search', scannedVal, NATIONALITY_LIST);
                }
                const searchEl = document.getElementById(f.elId + '-search');
                if (searchEl) {
                    searchEl.classList.add('bg-green-50', 'border-green-400');
                    setTimeout(() => { searchEl.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
                } else {
                    el.classList.add('bg-green-50', 'border-green-400');
                    setTimeout(() => { el.classList.remove('bg-green-50', 'border-green-400'); }, 2000);
                }
            }

            let filledCount = 0;
            for (const f of fieldMap) {
                let scannedVal = extracted[f.key] || '';
                if (!scannedVal) continue;
                if (f.isDate) scannedVal = convertDateForInput(scannedVal) || scannedVal;
                const el = document.getElementById(f.elId);
                if (!el) continue;
                const existingVal = el.value.trim();
                if (!existingVal) {
                    // Empty field — auto-fill silently
                    _applyFieldValue(f, scannedVal);
                    filledCount++;
                } else {
                    // Has existing data — check if different
                    const normExisting = existingVal.toLowerCase().replace(/[-\s\/]/g, '');
                    const normScanned = scannedVal.toLowerCase().replace(/[-\s\/]/g, '');
                    if (normExisting !== normScanned) {
                        conflicts.push({ ...f, existingVal, scannedVal });
                    }
                    // If same, do nothing — keep existing
                }
            }

            if (conflicts.length > 0) {
                if (statusEl) statusEl.innerHTML = '';
                showFieldConflictDialog(conflicts, (resolutions) => {
                    for (const c of conflicts) {
                        if (resolutions[c.key] === 'scanned') {
                            _applyFieldValue(c, c.scannedVal);
                        }
                    }
                    if (isPassport && extracted.passport_expiry) {
                        document.getElementById('passport-expiry-field').classList.remove('hidden');
                    }
                    if (extracted.nric_number) checkAndHandleDuplicate(extracted.nric_number);
                    showUnsavedBanner();
                    showAddressSuggestions();
                });
            } else {
                if (isPassport && extracted.passport_expiry) {
                    document.getElementById('passport-expiry-field').classList.remove('hidden');
                }
                if (extracted.nric_number) checkAndHandleDuplicate(extracted.nric_number);
                if (filledCount > 0) {
                    showUnsavedBanner();
                    showAddressSuggestions();
                    if (statusEl) statusEl.innerHTML = `<span class="text-green-600">✓ ${filledCount} field(s) filled from scan.</span>`;
                } else {
                    if (statusEl) statusEl.innerHTML = '<span class="text-green-600">✓ All data matches existing. No changes needed.</span>';
                }
            }
        } else if (data.ok && data.warning) {
            if (statusEl) statusEl.innerHTML = `<span class="text-amber-600">⚠ ${data.warning}</span>`;
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the document. Please try a clearer image.</span>';
        }
    } catch (e) {
        console.error('OCR scan error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Scan failed. Please try again.</span>';
    } finally {
        if (scanBtn) { scanBtn.disabled = false; scanBtn.classList.remove('opacity-50'); }
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
            let errMsg = 'Upload failed. Please try again.';
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
        } else if (data.ok && data.warning) {
            // OCR failed but file was saved
            if (statusEl) statusEl.innerHTML = `<span class="text-amber-600">⚠ ${data.warning}</span>`;
        } else {
            if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Could not read the financial document. Please try a clearer image.</span>';
        }
        // Add document to preview list regardless of OCR success
        if (data && data.ok) {
            _addGiftDocPreview(giftIndex, file.name, data.document_url || '', 'financial');
        }
    } catch (e) {
        console.error('Asset upload error:', e);
        if (statusEl) statusEl.innerHTML = '<span class="text-red-600">❌ Upload failed. Check your internet connection and try again.</span>';
    }
}

/**
 * Add a document preview entry to the gift's document list.
 */
function _addGiftDocPreview(giftIndex, fileName, docUrl, docType) {
    const container = document.getElementById('gift-docs-' + giftIndex);
    if (!container) return;
    container.classList.remove('hidden');

    const docId = 'gift-doc-' + giftIndex + '-' + Date.now();
    const typeLabel = docType === 'title' ? 'Title' :
                      docType === 'cukai_harta' ? 'Cukai Harta' :
                      docType === 'cukai_pintu' ? 'Cukai Pintu' :
                      docType === 'spa' ? 'SPA' :
                      docType === 'financial' ? 'Financial' :
                      docType === 'general' ? 'Document' : 'Document';
    // Get file extension for display
    const ext = fileName.split('.').pop().toUpperCase();

    const html = `
    <div id="${docId}" class="flex items-center gap-2 p-2 bg-white border border-gray-200 rounded-lg text-xs">
        <span class="px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded font-medium flex-shrink-0">${typeLabel}</span>
        <span class="text-gray-500 flex-shrink-0">${ext}</span>
        ${docUrl ? `<button type="button" onclick="window.open('${docUrl}', '_blank')" class="px-2.5 py-1 bg-blue-50 text-blue-600 rounded hover:bg-blue-100 font-medium flex-shrink-0">View</button>` : ''}
        <button type="button" onclick="if(confirm('Delete this document?')){document.getElementById('${docId}').remove(); var c=document.getElementById('gift-docs-${giftIndex}'); if(c && !c.children.length) c.classList.add('hidden');}"
                class="px-2.5 py-1 bg-red-50 text-red-600 rounded hover:bg-red-100 font-medium flex-shrink-0">Delete</button>
    </div>`;
    container.insertAdjacentHTML('beforeend', html);
}
