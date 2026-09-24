const React=require('react');const {renderToStaticMarkup}=require('react-dom/server');const sharp=require('sharp');
const fa=require('react-icons/fa');
const names=['FaShieldAlt','FaSearch','FaCogs','FaClipboardCheck','FaExclamationTriangle','FaFileAlt','FaEye','FaCode','FaLink','FaBan','FaChartLine','FaBolt','FaDatabase','FaLayerGroup','FaDesktop','FaPython','FaCheck','FaTimes','FaExclamation','FaLightbulb','FaBullseye','FaBug','FaRedo','FaStream','FaServer','FaRocket','FaFlask','FaBookOpen','FaBalanceScale','FaFingerprint','FaUserCheck','FaTools','FaProjectDiagram','FaFlagCheckered','FaHourglassHalf','FaRoute','FaMicrochip','FaClock','FaSitemap','FaGlobe','FaSyncAlt','FaCheckCircle','FaTasks','FaUsers','FaCalendarAlt','FaQuestionCircle','FaSlidersH','FaRegFileCode','FaCubes','FaHandPaper','FaPuzzlePiece','FaTachometerAlt','FaRegLightbulb','FaBrain','FaKey','FaLock','FaMapSigns','FaFilter','FaSitemap'];
const cols={white:'FFFFFF',maroon:'820019',navy:'002060',gold:'C99700',green:'1E8E4E'};
(async()=>{let miss=[],n=0;
for(const nm of [...new Set(names)]){const C=fa[nm];if(!C){miss.push(nm);continue;}
 for(const [cn,hex] of Object.entries(cols)){
  const svg=renderToStaticMarkup(React.createElement(C,{color:'#'+hex,size:'256'}));
  await sharp(Buffer.from(svg)).resize(256,256,{fit:'contain',background:{r:0,g:0,b:0,alpha:0}}).png().toFile(`icons/${nm}_${cn}.png`);n++;}}
console.log('made',n,'missing',miss);})();
