/**
 * Malaysian postcode to Mukim/Daerah lookup.
 * Auto-fills Mukim and Daerah fields based on postcode.
 * Coverage: major areas. Falls back gracefully for unknown postcodes.
 */
const MY_POSTCODE_MAP = {
    // Johor
    '79000': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79100': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79150': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79200': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79250': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79350': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79502': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79503': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79513': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79514': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79520': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79523': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79532': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79538': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79540': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79546': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79548': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79550': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79555': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '79570': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '79576': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80000': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80050': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80100': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80150': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80200': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80250': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80300': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80350': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80400': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80500': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80506': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80508': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80516': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80519': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80534': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80536': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80542': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80546': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80560': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80564': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80568': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80578': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80584': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80586': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80590': {mukim:'Johor Bahru',daerah:'Johor Bahru'}, '80592': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '80596': {mukim:'Johor Bahru',daerah:'Johor Bahru'},
    '81100': {mukim:'Plentong',daerah:'Johor Bahru'}, '81200': {mukim:'Plentong',daerah:'Johor Bahru'},
    '81300': {mukim:'Tebrau',daerah:'Johor Bahru'}, '81400': {mukim:'Pulai',daerah:'Johor Bahru'},
    '81500': {mukim:'Pulai',daerah:'Johor Bahru'}, '81700': {mukim:'Pasir Gudang',daerah:'Johor Bahru'},
    '81750': {mukim:'Pasir Gudang',daerah:'Johor Bahru'}, '81800': {mukim:'Ulu Tiram',daerah:'Johor Bahru'},
    '81900': {mukim:'Kota Tinggi',daerah:'Kota Tinggi'},
    '81000': {mukim:'Kulai',daerah:'Kulai'},
    '83000': {mukim:'Batu Pahat',daerah:'Batu Pahat'}, '83100': {mukim:'Batu Pahat',daerah:'Batu Pahat'},
    '84000': {mukim:'Muar',daerah:'Muar'}, '84200': {mukim:'Muar',daerah:'Muar'},
    '85000': {mukim:'Segamat',daerah:'Segamat'}, '85100': {mukim:'Segamat',daerah:'Segamat'},
    '86000': {mukim:'Kluang',daerah:'Kluang'}, '86100': {mukim:'Kluang',daerah:'Kluang'},
    // Selangor
    '40000': {mukim:'Klang',daerah:'Klang'}, '40100': {mukim:'Klang',daerah:'Klang'},
    '40150': {mukim:'Klang',daerah:'Klang'}, '40160': {mukim:'Klang',daerah:'Klang'},
    '40170': {mukim:'Klang',daerah:'Klang'}, '40200': {mukim:'Klang',daerah:'Klang'},
    '40300': {mukim:'Klang',daerah:'Klang'}, '40400': {mukim:'Klang',daerah:'Klang'},
    '40450': {mukim:'Klang',daerah:'Klang'}, '40460': {mukim:'Klang',daerah:'Klang'},
    '40470': {mukim:'Klang',daerah:'Klang'},
    '41000': {mukim:'Klang',daerah:'Klang'}, '41050': {mukim:'Klang',daerah:'Klang'},
    '41100': {mukim:'Klang',daerah:'Klang'}, '41150': {mukim:'Klang',daerah:'Klang'},
    '41200': {mukim:'Klang',daerah:'Klang'}, '41250': {mukim:'Klang',daerah:'Klang'},
    '41300': {mukim:'Kapar',daerah:'Klang'},
    '42700': {mukim:'Banting',daerah:'Kuala Langat'},
    '43000': {mukim:'Kajang',daerah:'Hulu Langat'}, '43100': {mukim:'Hulu Langat',daerah:'Hulu Langat'},
    '43200': {mukim:'Cheras',daerah:'Hulu Langat'}, '43300': {mukim:'Balakong',daerah:'Hulu Langat'},
    '43400': {mukim:'Serdang',daerah:'Petaling'}, '43500': {mukim:'Semenyih',daerah:'Hulu Langat'},
    '43600': {mukim:'Bangi',daerah:'Hulu Langat'}, '43650': {mukim:'Bangi',daerah:'Hulu Langat'},
    '43700': {mukim:'Beranang',daerah:'Hulu Langat'}, '43800': {mukim:'Dengkil',daerah:'Sepang'},
    '43900': {mukim:'Sepang',daerah:'Sepang'},
    '46000': {mukim:'Petaling',daerah:'Petaling'}, '46050': {mukim:'Petaling',daerah:'Petaling'},
    '46100': {mukim:'Petaling',daerah:'Petaling'}, '46150': {mukim:'Petaling',daerah:'Petaling'},
    '46200': {mukim:'Petaling',daerah:'Petaling'}, '46300': {mukim:'Petaling',daerah:'Petaling'},
    '46350': {mukim:'Petaling',daerah:'Petaling'}, '46400': {mukim:'Petaling',daerah:'Petaling'},
    '46506': {mukim:'Petaling',daerah:'Petaling'}, '46547': {mukim:'Petaling',daerah:'Petaling'},
    '46549': {mukim:'Petaling',daerah:'Petaling'}, '46551': {mukim:'Petaling',daerah:'Petaling'},
    '46564': {mukim:'Petaling',daerah:'Petaling'}, '46582': {mukim:'Petaling',daerah:'Petaling'},
    '46598': {mukim:'Petaling',daerah:'Petaling'},
    '47000': {mukim:'Sungai Buloh',daerah:'Petaling'}, '47100': {mukim:'Puchong',daerah:'Petaling'},
    '47120': {mukim:'Puchong',daerah:'Petaling'}, '47130': {mukim:'Puchong',daerah:'Petaling'},
    '47140': {mukim:'Puchong',daerah:'Petaling'}, '47150': {mukim:'Puchong',daerah:'Petaling'},
    '47160': {mukim:'Puchong',daerah:'Petaling'}, '47170': {mukim:'Puchong',daerah:'Petaling'},
    '47180': {mukim:'Puchong',daerah:'Petaling'}, '47190': {mukim:'Puchong',daerah:'Petaling'},
    '47200': {mukim:'Petaling',daerah:'Petaling'},
    '47300': {mukim:'Petaling',daerah:'Petaling'}, '47301': {mukim:'Petaling',daerah:'Petaling'},
    '47400': {mukim:'Petaling',daerah:'Petaling'}, '47410': {mukim:'Petaling',daerah:'Petaling'},
    '47500': {mukim:'Damansara',daerah:'Petaling'}, '47600': {mukim:'Damansara',daerah:'Petaling'},
    '47620': {mukim:'Damansara',daerah:'Petaling'}, '47630': {mukim:'Damansara',daerah:'Petaling'},
    '47650': {mukim:'Damansara',daerah:'Petaling'}, '47800': {mukim:'Petaling',daerah:'Petaling'},
    '47810': {mukim:'Petaling',daerah:'Petaling'},
    '48000': {mukim:'Rawang',daerah:'Gombak'}, '48020': {mukim:'Rawang',daerah:'Gombak'},
    '48050': {mukim:'Rawang',daerah:'Gombak'}, '48100': {mukim:'Rawang',daerah:'Gombak'},
    '48200': {mukim:'Selayang',daerah:'Gombak'}, '48300': {mukim:'Rawang',daerah:'Gombak'},
    '68000': {mukim:'Gombak',daerah:'Gombak'}, '68100': {mukim:'Batu Caves',daerah:'Gombak'},
    // KL
    '50000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50050': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50150': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50250': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50300': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50350': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50400': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50450': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50460': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50470': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50480': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50490': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50500': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50502': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50504': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50506': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50508': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50510': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50512': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50514': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50515': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50516': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50518': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50519': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50520': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50528': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50530': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50532': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50534': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50536': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50538': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50540': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50544': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50546': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50548': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50550': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50551': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50552': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50554': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50556': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50560': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50564': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50566': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50568': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50572': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50576': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50578': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50580': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50582': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50586': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50588': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50590': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50592': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50594': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50596': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50598': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50600': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50603': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '50604': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '50700': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '51000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '51100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '51200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '52000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '52100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '52200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '53000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '53100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '53200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '53300': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '54000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '54100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '54200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '55000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '55100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '55200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '55300': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '56000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '56100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '57000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '57100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '57200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '58000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '58100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '58200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '59000': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'}, '59100': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    '59200': {mukim:'Kuala Lumpur',daerah:'Kuala Lumpur'},
    // Penang
    '10000': {mukim:'Georgetown',daerah:'Timur Laut'}, '10050': {mukim:'Georgetown',daerah:'Timur Laut'},
    '10100': {mukim:'Georgetown',daerah:'Timur Laut'}, '10150': {mukim:'Georgetown',daerah:'Timur Laut'},
    '10200': {mukim:'Georgetown',daerah:'Timur Laut'}, '10250': {mukim:'Georgetown',daerah:'Timur Laut'},
    '10300': {mukim:'Georgetown',daerah:'Timur Laut'}, '10350': {mukim:'Georgetown',daerah:'Timur Laut'},
    '10400': {mukim:'Georgetown',daerah:'Timur Laut'}, '10450': {mukim:'Georgetown',daerah:'Timur Laut'},
    '10460': {mukim:'Georgetown',daerah:'Timur Laut'}, '10470': {mukim:'Georgetown',daerah:'Timur Laut'},
    '11600': {mukim:'Bayan Lepas',daerah:'Barat Daya'}, '11700': {mukim:'Gelugor',daerah:'Timur Laut'},
    '11900': {mukim:'Bayan Lepas',daerah:'Barat Daya'}, '11950': {mukim:'Bayan Lepas',daerah:'Barat Daya'},
    '13000': {mukim:'Butterworth',daerah:'Seberang Perai Utara'},
    '14000': {mukim:'Bukit Mertajam',daerah:'Seberang Perai Tengah'},
    // Perak
    '30000': {mukim:'Ipoh',daerah:'Kinta'}, '30010': {mukim:'Ipoh',daerah:'Kinta'},
    '30020': {mukim:'Ipoh',daerah:'Kinta'}, '30100': {mukim:'Ipoh',daerah:'Kinta'},
    '30200': {mukim:'Ipoh',daerah:'Kinta'}, '30250': {mukim:'Ipoh',daerah:'Kinta'},
    '30300': {mukim:'Ipoh',daerah:'Kinta'}, '30350': {mukim:'Ipoh',daerah:'Kinta'},
    '30450': {mukim:'Ipoh',daerah:'Kinta'}, '30500': {mukim:'Ipoh',daerah:'Kinta'},
    '30600': {mukim:'Ipoh',daerah:'Kinta'}, '30700': {mukim:'Ipoh',daerah:'Kinta'},
    '30750': {mukim:'Ipoh',daerah:'Kinta'}, '30800': {mukim:'Ipoh',daerah:'Kinta'},
    '30900': {mukim:'Ipoh',daerah:'Kinta'},
    '31400': {mukim:'Ipoh',daerah:'Kinta'}, '31450': {mukim:'Menglembu',daerah:'Kinta'},
    '31500': {mukim:'Lahat',daerah:'Kinta'}, '31550': {mukim:'Pusing',daerah:'Kinta'},
    '31600': {mukim:'Gopeng',daerah:'Kampar'}, '31650': {mukim:'Ipoh',daerah:'Kinta'},
    '31700': {mukim:'Simpang Pulai',daerah:'Kinta'}, '31750': {mukim:'Tanjung Rambutan',daerah:'Kinta'},
    '31800': {mukim:'Chemor',daerah:'Kinta'}, '31850': {mukim:'Chemor',daerah:'Kinta'},
    '31900': {mukim:'Kampar',daerah:'Kampar'},
    '32000': {mukim:'Sitiawan',daerah:'Manjung'}, '32400': {mukim:'Ayer Tawar',daerah:'Manjung'},
    '34000': {mukim:'Taiping',daerah:'Larut, Matang & Selama'},
    '36000': {mukim:'Teluk Intan',daerah:'Hilir Perak'},
    // Negeri Sembilan
    '70000': {mukim:'Seremban',daerah:'Seremban'}, '70100': {mukim:'Seremban',daerah:'Seremban'},
    '70200': {mukim:'Seremban',daerah:'Seremban'}, '70300': {mukim:'Seremban',daerah:'Seremban'},
    '70400': {mukim:'Seremban',daerah:'Seremban'}, '70450': {mukim:'Seremban',daerah:'Seremban'},
    '70500': {mukim:'Seremban',daerah:'Seremban'}, '70600': {mukim:'Seremban',daerah:'Seremban'},
    '71000': {mukim:'Port Dickson',daerah:'Port Dickson'},
    // Melaka
    '75000': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75050': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    '75100': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75150': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    '75200': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75250': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    '75300': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75350': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    '75400': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75450': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    '75460': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'}, '75500': {mukim:'Melaka Tengah',daerah:'Melaka Tengah'},
    // Pahang
    '25000': {mukim:'Kuantan',daerah:'Kuantan'}, '25050': {mukim:'Kuantan',daerah:'Kuantan'},
    '25100': {mukim:'Kuantan',daerah:'Kuantan'}, '25150': {mukim:'Kuantan',daerah:'Kuantan'},
    '25200': {mukim:'Kuantan',daerah:'Kuantan'}, '25250': {mukim:'Kuantan',daerah:'Kuantan'},
    '25300': {mukim:'Kuantan',daerah:'Kuantan'},
    // Kedah
    '05000': {mukim:'Alor Setar',daerah:'Kota Setar'}, '05050': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '05100': {mukim:'Alor Setar',daerah:'Kota Setar'}, '05150': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '05200': {mukim:'Alor Setar',daerah:'Kota Setar'}, '05250': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '05300': {mukim:'Alor Setar',daerah:'Kota Setar'}, '05350': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '05400': {mukim:'Alor Setar',daerah:'Kota Setar'}, '05450': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '05460': {mukim:'Alor Setar',daerah:'Kota Setar'},
    '08000': {mukim:'Sungai Petani',daerah:'Kuala Muda'},
    // Kelantan
    '15000': {mukim:'Kota Bharu',daerah:'Kota Bharu'}, '15050': {mukim:'Kota Bharu',daerah:'Kota Bharu'},
    '15100': {mukim:'Kota Bharu',daerah:'Kota Bharu'}, '15150': {mukim:'Kota Bharu',daerah:'Kota Bharu'},
    '15200': {mukim:'Kota Bharu',daerah:'Kota Bharu'},
    // Terengganu
    '20000': {mukim:'Kuala Terengganu',daerah:'Kuala Terengganu'},
    '20050': {mukim:'Kuala Terengganu',daerah:'Kuala Terengganu'},
    '20100': {mukim:'Kuala Terengganu',daerah:'Kuala Terengganu'},
    // Putrajaya
    '62000': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62100': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62200': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62300': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62502': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62505': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62506': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62510': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62512': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62514': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62516': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62517': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62518': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62519': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62520': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62530': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62532': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62542': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62546': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62550': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62551': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62570': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62574': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62576': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62582': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62584': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62590': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62592': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62596': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62602': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62604': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62605': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62606': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62616': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62618': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62620': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62623': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62624': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62626': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62628': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62630': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62632': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62652': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62654': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62662': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62668': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62670': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62674': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62675': {mukim:'Putrajaya',daerah:'Putrajaya'}, '62676': {mukim:'Putrajaya',daerah:'Putrajaya'},
    '62677': {mukim:'Putrajaya',daerah:'Putrajaya'},
};

/**
 * Look up Mukim and Daerah from postcode.
 * Auto-fills Mukim and Daerah fields if empty.
 */
function autoFillMukimDaerah(giftIndex) {
    const postcode = document.querySelector(`[name="gift_prop_postcode_${giftIndex}"]`)?.value?.trim() || '';
    if (!postcode || postcode.length !== 5) return;

    const lookup = MY_POSTCODE_MAP[postcode];
    if (!lookup) return;

    const mukimField = document.querySelector(`[name="gift_prop_bandar_${giftIndex}"]`);
    const daerahField = document.querySelector(`[name="gift_prop_daerah_${giftIndex}"]`);

    if (mukimField && !mukimField.value.trim()) {
        mukimField.value = lookup.mukim;
        mukimField.classList.add('bg-blue-50');
        mukimField.title = 'Auto-filled from postcode ' + postcode;
        setTimeout(() => mukimField.classList.remove('bg-blue-50'), 3000);
    }
    if (daerahField && !daerahField.value.trim()) {
        daerahField.value = lookup.daerah;
        daerahField.classList.add('bg-blue-50');
        daerahField.title = 'Auto-filled from postcode ' + postcode;
        setTimeout(() => daerahField.classList.remove('bg-blue-50'), 3000);
    }

    updatePropertyPreview(giftIndex);
}

/**
 * Mukim → Daerah lookup for major Malaysian mukim areas.
 * When Mukim is filled, auto-fill Daerah if empty.
 */
const MY_MUKIM_DAERAH_MAP = {
    // Johor
    'PLENTONG': 'Johor Bahru', 'TEBRAU': 'Johor Bahru', 'PULAI': 'Johor Bahru',
    'JOHOR BAHRU': 'Johor Bahru', 'JELUTONG': 'Johor Bahru', 'PASIR GUDANG': 'Johor Bahru',
    'ULU TIRAM': 'Johor Bahru', 'SENAI': 'Kulai', 'KULAI': 'Kulai',
    'BATU PAHAT': 'Batu Pahat', 'SIMPANG KANAN': 'Batu Pahat', 'SIMPANG KIRI': 'Batu Pahat',
    'MUAR': 'Muar', 'SERI MENANTI': 'Muar', 'BANDAR MAHARANI': 'Muar',
    'SEGAMAT': 'Segamat', 'KLUANG': 'Kluang', 'PONTIAN': 'Pontian',
    'KOTA TINGGI': 'Kota Tinggi', 'MERSING': 'Mersing', 'TANGKAK': 'Tangkak',
    'ISKANDAR PUTERI': 'Johor Bahru', 'NUSAJAYA': 'Johor Bahru',
    'GELANG PATAH': 'Johor Bahru', 'SKUDAI': 'Johor Bahru',
    // Selangor
    'PETALING': 'Petaling', 'DAMANSARA': 'Petaling', 'SUNGAI BULOH': 'Petaling',
    'PUCHONG': 'Petaling', 'SUBANG': 'Petaling', 'KELANA JAYA': 'Petaling',
    'KLANG': 'Klang', 'KAPAR': 'Klang', 'MERU': 'Klang',
    'KAJANG': 'Hulu Langat', 'CHERAS': 'Hulu Langat', 'BALAKONG': 'Hulu Langat',
    'SERDANG': 'Petaling', 'SEMENYIH': 'Hulu Langat', 'BANGI': 'Hulu Langat',
    'HULU LANGAT': 'Hulu Langat', 'BERANANG': 'Hulu Langat',
    'RAWANG': 'Gombak', 'GOMBAK': 'Gombak', 'SELAYANG': 'Gombak', 'BATU CAVES': 'Gombak',
    'SEPANG': 'Sepang', 'DENGKIL': 'Sepang',
    'KUALA SELANGOR': 'Kuala Selangor', 'BANTING': 'Kuala Langat',
    'HULU SELANGOR': 'Hulu Selangor', 'KUALA KUBU BHARU': 'Hulu Selangor',
    'SABAK BERNAM': 'Sabak Bernam',
    'SHAH ALAM': 'Petaling', 'CYBERJAYA': 'Sepang',
    // KL
    'KUALA LUMPUR': 'Kuala Lumpur', 'BATU': 'Kuala Lumpur', 'SETAPAK': 'Kuala Lumpur',
    'CHERAS KL': 'Kuala Lumpur', 'BANGSAR': 'Kuala Lumpur', 'KEPONG': 'Kuala Lumpur',
    'SEGAMBUT': 'Kuala Lumpur', 'WANGSA MAJU': 'Kuala Lumpur', 'TITIWANGSA': 'Kuala Lumpur',
    // Penang
    'GEORGETOWN': 'Timur Laut', 'TANJUNG BUNGAH': 'Timur Laut',
    'BAYAN LEPAS': 'Barat Daya', 'GELUGOR': 'Timur Laut', 'JELUTONG PG': 'Timur Laut',
    'BUTTERWORTH': 'Seberang Perai Utara', 'BUKIT MERTAJAM': 'Seberang Perai Tengah',
    'NIBONG TEBAL': 'Seberang Perai Selatan', 'BALIK PULAU': 'Barat Daya',
    // Perak
    'IPOH': 'Kinta', 'MENGLEMBU': 'Kinta', 'LAHAT': 'Kinta', 'GOPENG': 'Kampar',
    'KAMPAR': 'Kampar', 'CHEMOR': 'Kinta', 'TANJUNG RAMBUTAN': 'Kinta',
    'TAIPING': 'Larut, Matang & Selama', 'TELUK INTAN': 'Hilir Perak',
    'SITIAWAN': 'Manjung', 'LUMUT': 'Manjung',
    // Negeri Sembilan
    'SEREMBAN': 'Seremban', 'PORT DICKSON': 'Port Dickson', 'NILAI': 'Seremban',
    // Melaka
    'MELAKA TENGAH': 'Melaka Tengah', 'ALOR GAJAH': 'Alor Gajah', 'JASIN': 'Jasin',
    // Pahang
    'KUANTAN': 'Kuantan', 'TEMERLOH': 'Temerloh', 'BENTONG': 'Bentong',
    // Kedah
    'ALOR SETAR': 'Kota Setar', 'SUNGAI PETANI': 'Kuala Muda', 'KULIM': 'Kulim',
    'LANGKAWI': 'Langkawi',
    // Kelantan
    'KOTA BHARU': 'Kota Bharu',
    // Terengganu
    'KUALA TERENGGANU': 'Kuala Terengganu',
    // Putrajaya
    'PUTRAJAYA': 'Putrajaya',
};

function autoFillDaerahFromMukim(giftIndex) {
    const mukimField = document.querySelector(`[name="gift_prop_bandar_${giftIndex}"]`);
    const daerahField = document.querySelector(`[name="gift_prop_daerah_${giftIndex}"]`);
    if (!mukimField || !daerahField) return;
    if (daerahField.value.trim()) return; // Already has value

    const mukim = mukimField.value.trim().toUpperCase();
    if (!mukim) return;

    const daerah = MY_MUKIM_DAERAH_MAP[mukim];
    if (daerah) {
        daerahField.value = daerah;
        daerahField.classList.add('bg-blue-50');
        daerahField.title = 'Auto-filled from Mukim: ' + mukimField.value;
        setTimeout(() => daerahField.classList.remove('bg-blue-50'), 3000);
        updatePropertyPreview(giftIndex);
    }
}

/**
 * Daerah → State mapping for validation.
 */
const MY_DAERAH_STATE_MAP = {
    // Johor
    'JOHOR BAHRU': 'JOHOR', 'KULAI': 'JOHOR', 'BATU PAHAT': 'JOHOR', 'MUAR': 'JOHOR',
    'SEGAMAT': 'JOHOR', 'KLUANG': 'JOHOR', 'PONTIAN': 'JOHOR', 'KOTA TINGGI': 'JOHOR',
    'MERSING': 'JOHOR', 'TANGKAK': 'JOHOR', 'LEDANG': 'JOHOR',
    // Selangor
    'PETALING': 'SELANGOR', 'KLANG': 'SELANGOR', 'HULU LANGAT': 'SELANGOR', 'GOMBAK': 'SELANGOR',
    'SEPANG': 'SELANGOR', 'KUALA SELANGOR': 'SELANGOR', 'KUALA LANGAT': 'SELANGOR',
    'HULU SELANGOR': 'SELANGOR', 'SABAK BERNAM': 'SELANGOR',
    // KL
    'KUALA LUMPUR': 'W.P. KUALA LUMPUR',
    // Penang
    'TIMUR LAUT': 'PULAU PINANG', 'BARAT DAYA': 'PULAU PINANG',
    'SEBERANG PERAI UTARA': 'PULAU PINANG', 'SEBERANG PERAI TENGAH': 'PULAU PINANG',
    'SEBERANG PERAI SELATAN': 'PULAU PINANG',
    // Perak
    'KINTA': 'PERAK', 'KAMPAR': 'PERAK', 'MANJUNG': 'PERAK',
    'LARUT, MATANG & SELAMA': 'PERAK', 'HILIR PERAK': 'PERAK',
    'BATANG PADANG': 'PERAK', 'PERAK TENGAH': 'PERAK',
    // N.Sembilan
    'SEREMBAN': 'NEGERI SEMBILAN', 'PORT DICKSON': 'NEGERI SEMBILAN',
    'JEMPOL': 'NEGERI SEMBILAN', 'KUALA PILAH': 'NEGERI SEMBILAN',
    // Melaka
    'MELAKA TENGAH': 'MELAKA', 'ALOR GAJAH': 'MELAKA', 'JASIN': 'MELAKA',
    // Pahang
    'KUANTAN': 'PAHANG', 'TEMERLOH': 'PAHANG', 'BENTONG': 'PAHANG', 'RAUB': 'PAHANG',
    // Kedah
    'KOTA SETAR': 'KEDAH', 'KUALA MUDA': 'KEDAH', 'KULIM': 'KEDAH', 'LANGKAWI': 'KEDAH',
    // Kelantan
    'KOTA BHARU': 'KELANTAN',
    // Terengganu
    'KUALA TERENGGANU': 'TERENGGANU',
    // Putrajaya
    'PUTRAJAYA': 'W.P. PUTRAJAYA',
};

/**
 * Validate property data for consistency.
 * Returns array of {field, message, severity} warnings.
 */
function validatePropertyData(giftIndex) {
    const addr = document.querySelector(`[name="gift_prop_address_${giftIndex}"]`)?.value?.trim() || '';
    const mukim = document.querySelector(`[name="gift_prop_bandar_${giftIndex}"]`)?.value?.trim() || '';
    const daerah = document.querySelector(`[name="gift_prop_daerah_${giftIndex}"]`)?.value?.trim() || '';
    const negeri = document.querySelector(`[name="gift_prop_negeri_${giftIndex}"]`)?.value?.trim() || '';
    const titleType = document.querySelector(`[name="gift_prop_title_type_${giftIndex}"]`)?.value || '';
    const titleNum = document.querySelector(`[name="gift_prop_title_number_${giftIndex}"]`)?.value?.trim() || '';
    const lotNum = document.querySelector(`[name="gift_prop_lot_number_${giftIndex}"]`)?.value?.trim() || '';

    const warnings = [];
    const postcode = document.querySelector(`[name="gift_prop_postcode_${giftIndex}"]`)?.value?.trim() || '';

    // 1. Daerah-State mismatch — with fix recommendation
    if (daerah && negeri) {
        const daerahUpper = daerah.toUpperCase().replace(/^DAERAH\s+/i, '');
        const expectedState = MY_DAERAH_STATE_MAP[daerahUpper];
        if (expectedState) {
            const negeriUpper = negeri.toUpperCase();
            if (!negeriUpper.includes(expectedState) && !expectedState.includes(negeriUpper.split(' ')[0])) {
                const fullStateName = (typeof MY_STATE_FULL_NAMES !== 'undefined') ? (MY_STATE_FULL_NAMES[expectedState] || expectedState) : expectedState;
                warnings.push({
                    field: 'negeri',
                    message: `Daerah "${daerah}" is in ${expectedState}, but Negeri is "${negeri}".`,
                    severity: 'error',
                    fix: { label: `Change to ${fullStateName}`, action: `fixNegeri(${giftIndex}, '${fullStateName.replace(/'/g,"\\'")}')` }
                });
            }
        }
    }

    // 2. Address duplicates — with fix to clean them
    if (addr && daerah) {
        const daerahClean = daerah.replace(/^daerah\s+/i, '').replace(/^district\s+of\s+/i, '');
        const daerahRegex = new RegExp(daerahClean.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
        const matches = addr.match(daerahRegex);
        if (matches && matches.length > 1) {
            // Build cleaned address
            let cleaned = addr;
            // Remove all but first occurrence of the city/daerah name after a comma
            let count = 0;
            cleaned = cleaned.replace(new RegExp(',\\s*' + daerahClean.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:\\s*,|\\s*$)', 'gi'), (m) => {
                count++;
                return count > 1 ? '' : m;
            });
            cleaned = cleaned.replace(/,\s*,/g, ',').replace(/,\s*$/, '').trim();
            warnings.push({
                field: 'address',
                message: `"${daerahClean}" appears ${matches.length} times in the address.`,
                severity: 'warning',
                fix: { label: 'Remove duplicates', action: `fixAddress(${giftIndex}, '${cleaned.replace(/'/g,"\\'")}')` }
            });
        }
    }

    // 3. Address completeness
    if (addr) {
        const parts = addr.split(',').map(p => p.trim());
        if (parts.length < 2) {
            warnings.push({
                field: 'address',
                message: 'Address may be incomplete — add street name and area/taman.',
                severity: 'warning',
                fix: null
            });
        }
    }

    // 4. Missing Mukim — recommend from postcode lookup
    if (!mukim) {
        const lookup = postcode ? MY_POSTCODE_MAP[postcode] : null;
        if (lookup) {
            warnings.push({
                field: 'mukim',
                message: `Mukim is required. Based on postcode ${postcode}:`,
                severity: 'error',
                fix: { label: `Use "${lookup.mukim}"`, action: `fixMukim(${giftIndex}, '${lookup.mukim}')` }
            });
        } else {
            warnings.push({field: 'mukim', message: 'Mukim is required for land search. Check your title document.', severity: 'error', fix: null});
        }
    }

    // 5. Missing Daerah — recommend from mukim or postcode lookup
    if (!daerah) {
        const mukimUpper = mukim.toUpperCase().replace(/^MUKIM\s+/i, '').replace(/^BANDAR\s+/i, '');
        const fromMukim = MY_MUKIM_DAERAH_MAP[mukimUpper];
        const lookup = postcode ? MY_POSTCODE_MAP[postcode] : null;
        const recommended = fromMukim || (lookup ? lookup.daerah : null);
        if (recommended) {
            warnings.push({
                field: 'daerah',
                message: `Daerah is required.${fromMukim ? ' Based on Mukim "'+mukim+'":' : ' Based on postcode '+postcode+':'}`,
                severity: 'error',
                fix: { label: `Use "${recommended}"`, action: `fixDaerah(${giftIndex}, '${recommended}')` }
            });
        } else {
            warnings.push({field: 'daerah', message: 'Daerah is required for land search. Check your title document.', severity: 'error', fix: null});
        }
    }

    // 6. Missing Negeri — recommend from daerah
    if (!negeri && daerah) {
        const daerahUpper = daerah.toUpperCase().replace(/^DAERAH\s+/i, '');
        const stateCode = MY_DAERAH_STATE_MAP[daerahUpper];
        if (stateCode) {
            const fullName = (typeof MY_STATE_FULL_NAMES !== 'undefined') ? (MY_STATE_FULL_NAMES[stateCode] || stateCode) : stateCode;
            warnings.push({
                field: 'negeri',
                message: `Negeri is required. Based on Daerah "${daerah}":`,
                severity: 'error',
                fix: { label: `Use "${fullName}"`, action: `fixNegeri(${giftIndex}, '${fullName.replace(/'/g,"\\'")}')` }
            });
        }
    } else if (!negeri) {
        warnings.push({field: 'negeri', message: 'Negeri (State) is required.', severity: 'error', fix: null});
    }

    // 7. Other required fields
    if (!titleType) warnings.push({field: 'title_type', message: 'Title Type is required. Upload title document to auto-detect.', severity: 'error', fix: null});
    if (!titleNum) warnings.push({field: 'title_number', message: 'Title Number is required. Check your title document.', severity: 'error', fix: null});
    if (!lotNum) warnings.push({field: 'lot_number', message: 'Lot Number is required. Check your title document.', severity: 'error', fix: null});

    return warnings;
}

// Quick-fix functions called from warning buttons
function fixNegeri(gi, val) {
    const el = document.querySelector(`[name="gift_prop_negeri_${gi}"]`);
    if (el) { el.value = val; updatePropertyPreview(gi); }
}
function fixDaerah(gi, val) {
    const el = document.querySelector(`[name="gift_prop_daerah_${gi}"]`);
    if (el) { el.value = val; updatePropertyPreview(gi); }
}
function fixMukim(gi, val) {
    const el = document.querySelector(`[name="gift_prop_bandar_${gi}"]`);
    if (el) { el.value = val; updatePropertyPreview(gi); }
}
function fixAddress(gi, val) {
    const el = document.querySelector(`[name="gift_prop_address_${gi}"]`);
    if (el) { el.value = val; updatePropertyPreview(gi); }
}

/**
 * Show validation warnings with fix buttons below property preview.
 */
function showPropertyWarnings(giftIndex) {
    const warnings = validatePropertyData(giftIndex);
    const previewEl = document.getElementById('property-preview-' + giftIndex);
    if (!previewEl) return;

    // Remove old warnings
    const oldWarnings = previewEl.parentElement.querySelector('.prop-warnings');
    if (oldWarnings) oldWarnings.remove();

    if (warnings.length === 0) return;

    const errors = warnings.filter(w => w.severity === 'error');
    const warns = warnings.filter(w => w.severity === 'warning');

    let html = '<div class="prop-warnings mt-2 space-y-1">';
    errors.forEach(w => {
        html += `<div class="flex items-start gap-1.5 text-xs text-red-600"><span>⚠</span><span>${w.message}</span></div>`;
    });
    warns.forEach(w => {
        html += `<div class="flex items-start gap-1.5 text-xs text-amber-600"><span>⚡</span><span>${w.message}</span></div>`;
    });
    html += '</div>';

    previewEl.insertAdjacentHTML('afterend', html);
}
