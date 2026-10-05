'use strict';
const $ = id => document.getElementById(id);
const canvas = $('goBoard');
const COLS = 'ABCDEFGHJKLMNOPQRST';
let game = null, busy = false, scanToken = 0, scanning = false;
let analyses = new Map(), preview = null, dead = new Set();
let hover = null;

async function api(path, body, signal) {
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
        headers: body === undefined ? {} : {'Content-Type': 'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: signal || AbortSignal.timeout(100000)});
    const data = await response.json();
    if (!response.ok) {
        const error = new Error(typeof data.detail === 'string' ? data.detail : '参数不正确，请检查后重试');
        error.status = response.status;
        throw error;
    }
    return data;
}

function notice(message = '') {
    $('notice').textContent = message;
    $('notice').hidden = !message;
}

async function run(task, message) {
    if (busy) return;
    stopScan();
    busy = true;
    notice();
    render();
    if (message) $('status').textContent = message;
    try { await task(); }
    catch (error) {
        if (error.status === 409 && game) {
            try { accept(await api(`/api/games/${game.id}`)); } catch (_) { /* Keep the last confirmed board. */ }
        }
        notice(error.name === 'TimeoutError' ? '等待超时，可重试；已落下的棋仍会保存。' : error.message || '连接失败，请确认本地服务仍在运行');
        if ($('newDialog').open) $('newError').textContent = error.message;
    } finally {
        busy = false;
        render();
    }
}

function accept(state) {
    if (game?.id !== state.id) $('scanStatus').textContent = '初筛会逐局面计算，可随时停止。';
    game = state;
    localStorage.setItem('go-study-active', game.id);
    preview = null;
    dead = new Set(game.dead);
    hover = null;
    render();
}

async function mutate(action, extra = {}) {
    accept(await api(`/api/games/${game.id}/${action}`, {revision: game.revision, ...extra}));
}

async function continueAI() {
    if (game.mode === 'play' && game.status === 'playing' && !game.handicap_left && game.player !== game.human_color) {
        $('status').textContent = 'KataGo 正在思考，首次运行可能需要稍候…';
        await mutate('ai', {visits: Number($('aiVisits').value)});
    }
    if (game.status === 'scoring' && !game.score) await mutate('score', {dead: []});
}

async function refreshLibrary() {
    const games = await api('/api/games');
    $('library').replaceChildren();
    if (!games.length) {
        const empty = document.createElement('p'); empty.className = 'muted small';
        empty.textContent = '第一盘棋，从这里开始。'; $('library').append(empty);
    }
    games.forEach(item => {
        const button = document.createElement('button');
        button.className = 'library-item' + (game?.id === item.id ? ' active' : '');
        const title = document.createElement('strong'), detail = document.createElement('small');
        title.textContent = item.title;
        const date = new Date(item.updated).toLocaleDateString('zh-CN', {month: 'short', day: 'numeric'});
        detail.textContent = `${item.size} 路 · ${item.result} · ${date}`;
        button.append(title, detail); button.disabled = busy;
        button.onclick = () => run(() => openGame(item.id), '正在打开棋谱…');
        $('library').append(button);
    });
}

async function openGame(id) {
    const state = await api(`/api/games/${id}`);
    analyses = new Map();
    accept(state);
    const saved = await api(`/api/games/${id}/analyses`);
    analyses = new Map(saved.map(item => [item.node, item]));
    await refreshLibrary();
}

function coordinate(action, size) {
    return action === size*size ? '停着' : `${COLS[action % size]}${size - Math.floor(action / size)}`;
}

function toAction(move, size) {
    if (move.toLowerCase() === 'pass') return size*size;
    return (size - Number(move.slice(1)))*size + COLS.indexOf(move[0]);
}

function turnText() {
    if (!game) return '点击“开始新对局”设置棋盘';
    if (preview) return '正在查看推荐变化，点击“返回实战”退出';
    if (game.handicap_left) return `请继续放置 ${game.handicap_left} 颗黑棋让子`;
    if (game.mode === 'review') return `轮到${game.player === 1 ? '黑' : '白'}方 · 点击棋盘可试下新分支`;
    if (game.status === 'scoring') return '双方连续停着 · 请确认死子与终局';
    if (game.status === 'finished') return `本局已结束 · ${game.result}，可以进入复盘`;
    return game.player === game.human_color ? `轮到你了 · 执${game.player === 1 ? '黑' : '白'}` : '轮到 KataGo · 点击“继续对弈”';
}

function render() {
    const review = game?.mode === 'review';
    const scoring = game?.mode === 'play' && game.status === 'scoring';
    const aiTurn = game && game.mode === 'play' && game.status === 'playing' && !game.handicap_left && game.player !== game.human_color;
    document.body.classList.toggle('busy', busy);
    $('status').textContent = turnText();
    $('modeBadge').textContent = !game ? '准备开始' : review ? '复盘研习' : game.status === 'finished' ? '已完成' : '与 AI 对弈';
    $('gameTitle').textContent = game ? `${game.black_name} · ${game.white_name}` : '一方棋盘，一次新的思考';
    $('gameMeta').textContent = game ? `${game.size} 路棋盘 · ${game.rules === 'Chinese' ? '中国数子' : game.rules === 'Japanese' ? '日本规则' : '韩国规则'} · 白贴 ${game.komi} 目` : '选择对局，或导入一盘值得回看的棋。';
    $('moveCount').textContent = `第 ${game?.step || 0} 手`;
    $('turnDot').classList.toggle('white', game?.player === -1 && !game.handicap_left);
    $('saveState').textContent = game ? '已自动保存' : '本地研习空间';
    ['newGame','importGame','refreshLibrary','openStudy'].forEach(id => $(id).disabled = busy);
    $('generateStudy').disabled = !review || busy || scanning || !!game?.handicap_left;
    $('saveStudy').disabled = !review || busy || scanning || !!preview || game?.cursor === 'root' || !!game?.handicap_left;
    document.querySelectorAll('.library-item').forEach(el => el.disabled = busy);
    $('exportGame').disabled = !game || busy || !!game.handicap_left;
    $('enterReview').hidden = !!review;
    $('enterReview').disabled = !game || busy || !!game.handicap_left;
    $('playControls').hidden = !!review;
    $('reviewControls').hidden = !review;
    $('scoringPanel').hidden = !scoring;
    $('pass').disabled = !game || busy || !!preview || !!game.handicap_left || game.status !== 'playing' || !!aiTurn;
    $('undo').disabled = !game || busy || !!game.handicap_left || game.cursor === 'root' || game.status === 'finished';
    $('resign').disabled = !game || busy || !!game.handicap_left || game.status === 'finished';
    $('retryAI').hidden = !aiTurn;
    $('retryAI').disabled = busy;
    $('analyze').disabled = !game || busy || scanning || !!game.handicap_left || !!preview;
    $('scan').disabled = !game || busy || !review;
    $('scan').textContent = scanning ? '停止初筛' : '全谱初筛';
    ['previewScore', 'confirmScore', 'resumeGame'].forEach(id => $(id).disabled = busy);
    if (game) {
        const index = game.route.indexOf(game.cursor);
        $('timeline').max = game.route.length-1;
        $('timeline').value = index;
        $('timeline').disabled = busy || !!preview;
        $('first').disabled = $('previous').disabled = busy || index <= 0 || !!preview;
        $('next').disabled = $('last').disabled = busy || index === game.route.length-1 || !!preview;
        $('mainline').disabled = busy || !!preview;
        $('reviewPass').disabled = busy || !!preview;
        $('branches').replaceChildren();
        game.children.forEach((child, index) => {
            const button = document.createElement('button');
            button.textContent = `${index === 0 ? '①' : '↳'} ${child.label}`;
            button.disabled = busy || !!preview;
            button.onclick = () => goTo(child.id);
            $('branches').append(button);
        });
        if (!game.children.length) $('branches').textContent = '当前为分支末尾';
        $('comment').textContent = game.comment;
        $('commentPanel').hidden = !game.comment;
        $('scoreText').textContent = game.score ? `黑 ${game.score.black} · 白 ${game.score.white}（含贴目） · ${game.score.result}` : '标记后点击预览数子';
    }
    $('previewBar').hidden = !preview;
    if (preview) {
        $('previewLabel').textContent = `变化 ${preview.index+1} / ${preview.frames.length} 手`;
        $('pvBack').disabled = preview.index <= 0;
        $('pvNext').disabled = preview.index >= preview.frames.length-1;
    }
    renderAnalysis();
    drawBoard();
}

function drawBoard() {
    paintBoard(canvas, game, preview, game && analyses.get(game.cursor), dead, hover, $('showOwnership').checked);
}

function paintBoard(target, state, variation, analysis, deadStones, hoverPoint, ownership = false) {
    const canvas = target, ctx = target.getContext('2d'), game = state, preview = variation, dead = deadStones, hover = hoverPoint;
    const size = game?.size || 19, width = canvas.width, margin = 36, cell = (width-2*margin)/(size-1);
    const frame = preview?.frames[preview.index];
    const board = frame?.board || game?.board || Array.from({length:size}, () => Array(size).fill(0));
    ctx.clearRect(0,0,width,width);
    const bg = ctx.createLinearGradient(0,0,width,width); bg.addColorStop(0,'#e4c895'); bg.addColorStop(1,'#d9b77e');
    ctx.fillStyle = bg; ctx.fillRect(0,0,width,width);
    ctx.strokeStyle = '#865e3310'; ctx.lineWidth = 1;
    for (let y=3; y<width; y+=7) { ctx.beginPath(); ctx.moveTo(0,y); ctx.bezierCurveTo(180,y-5,530,y+5,width,y+1); ctx.stroke(); }
    ctx.strokeStyle='#745533'; ctx.lineWidth=1.15;
    for(let i=0;i<size;i++) { const xy=margin+i*cell; ctx.beginPath();ctx.moveTo(margin,xy);ctx.lineTo(width-margin,xy);ctx.moveTo(xy,margin);ctx.lineTo(xy,width-margin);ctx.stroke(); }
    ctx.font='13px Segoe UI'; ctx.fillStyle='#7a5d3c'; ctx.textAlign='center'; ctx.textBaseline='middle';
    for(let i=0;i<size;i++){const xy=margin+i*cell;ctx.fillText(COLS[i],xy,15);ctx.fillText(COLS[i],xy,width-13);ctx.fillText(String(size-i),15,xy);ctx.fillText(String(size-i),width-14,xy);}
    const stars=size===19?[3,9,15]:size===13?[3,6,9]:[2,4,6];
    for(const r of stars) for(const c of stars){if(size!==19 && (r===stars[1]) !== (c===stars[1]))continue;ctx.beginPath();ctx.arc(margin+c*cell,margin+r*cell,size===19?3.2:4,0,Math.PI*2);ctx.fill();}
    if (ownership && analysis && !preview && game.status === 'playing') {
        analysis.ownership.forEach((value, action) => {
            ctx.fillStyle = value>0 ? `rgba(30,57,46,${Math.abs(value)*.25})` : `rgba(255,255,246,${Math.abs(value)*.7})`;
            ctx.fillRect(margin+(action%size-.45)*cell,margin+(Math.floor(action/size)-.45)*cell,cell*.9,cell*.9);
        });
    }
    for(let r=0;r<size;r++) for(let c=0;c<size;c++){
        const color=board[r][c]; if(!color)continue;
        const x=margin+c*cell,y=margin+r*cell,radius=cell*.45;
        ctx.globalAlpha=(!preview && dead.has(r*size+c)) ? 0.35 : 1;
        const gradient=ctx.createRadialGradient(x-radius*.35,y-radius*.4,radius*.1,x,y,radius);
        gradient.addColorStop(0,color===1?'#626763':'#fffefa');gradient.addColorStop(1,color===1?'#121a16':'#dce0d4');
        ctx.shadowColor='#45331850';ctx.shadowBlur=4;ctx.shadowOffsetY=2;
        ctx.beginPath();ctx.arc(x,y,radius,0,Math.PI*2);ctx.fillStyle=gradient;ctx.fill();
        ctx.shadowColor='transparent';ctx.globalAlpha=1;
    }
    if (!preview && game?.score) {
        game.score.territory.forEach((owner, action) => {
            const row=Math.floor(action/size), col=action%size;
            if (!owner || (board[row][col] && !dead.has(action))) return;
            ctx.fillStyle=owner===1?'#233b2ee0':'#fffef3e8';
            ctx.fillRect(margin+col*cell-cell*.13,margin+row*cell-cell*.13,cell*.26,cell*.26);
        });
    }
    let last=frame?.action;
    if(last===undefined&&game?.moves.length)last=toAction(game.moves.at(-1)[1],size);
    if(last!==undefined&&last!==null&&last<size*size){const r=Math.floor(last/size),c=last%size;ctx.strokeStyle=board[r][c]===1?'#e6c98a':'#597459';ctx.lineWidth=2;ctx.strokeRect(margin+c*cell-cell*.12,margin+r*cell-cell*.12,cell*.24,cell*.24);}
    if(analysis&&!preview&&game?.status!=='scoring') analysis.candidates.forEach((item,index)=>{
        const action=toAction(item.move,size);if(action>=size*size)return;
        const x=margin+action%size*cell,y=margin+Math.floor(action/size)*cell;
        ctx.fillStyle=index===0?'#285c4ae8':'#f4f4dfed';ctx.strokeStyle='#3a6653';ctx.lineWidth=1.5;
        ctx.beginPath();ctx.arc(x,y,cell*.34,0,Math.PI*2);ctx.fill();ctx.stroke();
        ctx.fillStyle=index===0?'#fff':'#345740';ctx.font=`600 ${Math.max(13,cell*.35)}px Segoe UI`;ctx.fillText('ABCDE'[index],x,y);
    });
    if(hover!==null&&!busy&&game&&!preview&&game.legal.includes(hover)&&game.status!=='scoring'){
        const r=Math.floor(hover/size),c=hover%size;
        if(!board[r][c]){ctx.globalAlpha=.25;ctx.fillStyle=(game.handicap_left||game.player===1)?'#172e20':'#fff';ctx.beginPath();ctx.arc(margin+c*cell,margin+r*cell,cell*.45,0,Math.PI*2);ctx.fill();ctx.globalAlpha=1;}
    }
}

function renderAnalysis() {
    const analysis = game && analyses.get(game.cursor);
    $('winrate').textContent = analysis ? `${(analysis.black_winrate*100).toFixed(1)}%` : '—';
    $('lead').textContent = analysis ? `${analysis.black_lead>=0?'黑 +':'白 +'}${Math.abs(analysis.black_lead).toFixed(1)}` : '—';
    $('winbarBlack').style.width = `${analysis ? analysis.black_winrate*100 : 50}%`;
    $('analysisNote').textContent = analysis ? `本次实际搜索 ${analysis.visits} 次 · 胜率及目差均为黑方视角` : '尚未分析此局面，点击“分析当前”查看。';
    $('candidates').replaceChildren();
    if (!analysis) {
        $('candidates').innerHTML = '<div class="empty"><span>◎</span><p>先思考，再看推荐</p><small>分析会保留多个值得比较的选择。</small></div>';
    } else analysis.candidates.forEach((item,index)=>{
        const button=document.createElement('button');button.className='candidate';button.disabled=busy;
        const letter=document.createElement('span');letter.className='letter';letter.textContent='ABCDE'[index];
        const label=document.createElement('div');const name=document.createElement('strong');name.textContent=item.move==='pass'?'停着':item.move;
        const description=document.createElement('small');description.textContent=index===0?'引擎首选':`候选 ${index+1} · ${item.visits} 次搜索`;label.append(name,description);
        const metric=document.createElement('div');metric.className='candidate-metric';metric.textContent=`${(item.winrate*100).toFixed(1)}%`;
        const lead=document.createElement('small');lead.textContent=`黑方 ${item.scoreLead>=0?'+':''}${item.scoreLead.toFixed(1)} 目`;metric.append(lead);
        button.append(letter,label,metric);button.onclick=()=>run(async()=>{
            const result=await api(`/api/games/${game.id}/preview`,{node:game.cursor,moves:item.pv});
            if(!result.frames.length)throw new Error('此候选暂未返回可回放的变化');
            preview={frames:result.frames,index:0};
        },'正在准备变化…');$('candidates').append(button);
    });
    renderChart();
}

function renderChart() {
    const route=game?.route||[], values=route.map(node=>analyses.get(node));
    const max=Math.max(10,...values.filter(Boolean).map(a=>Math.abs(a.black_lead)));
    const x=i=>12+i/Math.max(1,route.length-1)*276, y=v=>58-v/max*43;
    let svg='<line x1="12" x2="288" y1="58" y2="58" stroke="#d5d9ce" stroke-dasharray="3 3"/><text x="12" y="12" fill="#8a9383" font-size="9">黑优</text><text x="12" y="112" fill="#8a9383" font-size="9">白优</text>';
    values.forEach((a,i)=>{if(!a)return;if(i&&values[i-1])svg+=`<line x1="${x(i-1)}" y1="${y(values[i-1].black_lead)}" x2="${x(i)}" y2="${y(a.black_lead)}" stroke="#477452" stroke-width="2"/>`;svg+=`<circle cx="${x(i)}" cy="${y(a.black_lead)}" r="${game.cursor===route[i]?4:2}" fill="#477452"/>`;});
    if(!values.some(Boolean))svg+='<text x="150" y="48" text-anchor="middle" fill="#a1a697" font-size="11">分析后呈现局面变化</text>';
    $('chart').innerHTML=svg;
    $('mistakes').replaceChildren();
    const losses=[];
    for(let i=1;i<route.length;i++){
        if(!values[i-1]||!values[i])continue;
        // This is a swing estimate, not a claim about the cause or a formal move grade.
        const swing=Math.abs(values[i-1].black_lead-values[i].black_lead);
        if(swing>=2)losses.push({i,swing});
    }
    losses.sort((a,b)=>b.swing-a.swing).slice(0,3).forEach(({i,swing})=>{
        const button=document.createElement('button');button.className='mistake';button.disabled=busy;
        button.textContent=`节点 ${i} · 目差变化约 ${swing.toFixed(1)} · 回看 →`;
        button.onclick=()=>goTo(route[i-1]);$('mistakes').append(button);
    });
}

function stopScan() {
    if (scanning) $('scanStatus').textContent = '已停止；已完成的结果已保存。';
    scanToken++;
    scanning=false;
}
async function scan() {
    if(scanning){stopScan();$('scanStatus').textContent='已停止；已完成的结果已保存。';render();return;}
    if(!game||busy)return;
    const token=++scanToken,id=game.id,route=[...game.route];scanning=true;render();notice();
    try{
        for(let i=0;i<route.length;i++){
            if(token!==scanToken)break;
            $('scanStatus').textContent=`初筛 ${i+1} / ${route.length} 个局面 · 可点击停止`;
            const result=await api(`/api/games/${id}/analysis`,{node:route[i],visits:100});
            if(game?.id===id){analyses.set(result.node,result);render();}
        }
        if(token===scanToken)$('scanStatus').textContent='本分支初筛完成。目差突变仅作回看线索，请深入验证。';
    }catch(error){if(token===scanToken)notice(error.message);}
    finally{if(token===scanToken){scanning=false;render();}}
}

function goTo(node) {if(!game||game.mode!=='review')return;run(()=>mutate('navigate',{node}));}
function boardPoint(event){const rect=canvas.getBoundingClientRect(),size=game?.size||19,cell=(760-72)/(size-1);const c=Math.round(((event.clientX-rect.left)*760/rect.width-36)/cell),r=Math.round(((event.clientY-rect.top)*760/rect.height-36)/cell);return r>=0&&r<size&&c>=0&&c<size?r*size+c:null;}

canvas.addEventListener('mousemove',event=>{hover=boardPoint(event);drawBoard();});
canvas.addEventListener('mouseleave',()=>{hover=null;drawBoard();});
canvas.addEventListener('click',event=>{
    if(!game||busy||preview)return;
    const action=boardPoint(event);if(action===null)return;
    if(game.mode==='play'&&game.status==='scoring'){
        const size=game.size,color=game.board[Math.floor(action/size)][action%size];if(!color)return;
        const group=new Set([action]),pending=[action];
        while(pending.length){const p=pending.pop(),r=Math.floor(p/size),c=p%size;for(const [nr,nc] of [[r-1,c],[r+1,c],[r,c-1],[r,c+1]]){const q=nr*size+nc;if(nr>=0&&nr<size&&nc>=0&&nc<size&&game.board[nr][nc]===color&&!group.has(q)){group.add(q);pending.push(q);}}}
        const remove=dead.has(action);group.forEach(p=>remove?dead.delete(p):dead.add(p));
        const selected=[...dead];run(()=>mutate('score',{dead:selected}));return;
    }
    run(async()=>{await mutate('move',{action});await continueAI();await refreshLibrary();},'正在落子…');
});
$('newGame').onclick=()=>{$('newError').textContent='';$('newDialog').showModal();};
$('closeDialog').onclick=()=>$('newDialog').close();
$('newHandicap').onchange=()=>{const has=Number($('newHandicap').value)>0;if(has)$('newColor').value='1';$('newKomi').value=has?'0.5':'7.5';};
$('newMode').onchange=()=>{const review=$('newMode').value==='review';$('newHandicap').disabled=$('newColor').disabled=review;if(review)$('newHandicap').value='0';};
$('newForm').onsubmit=event=>{
    event.preventDefault();if(busy)return;
    run(async()=>{
        const state=await api('/api/games',{size:Number($('newSize').value),komi:Number($('newKomi').value),human_color:Number($('newColor').value),handicap:Number($('newHandicap').value),mode:$('newMode').value});
        analyses=new Map();accept(state);$('newDialog').close();await refreshLibrary();await continueAI();
    },'准备新对局…');
};
$('importGame').onclick=()=>$('sgfFile').click();
$('sgfFile').onchange=()=>{
    const file=$('sgfFile').files[0];$('sgfFile').value='';if(!file)return;
    run(async()=>{
        if(file.size>2_000_000)throw new Error('棋谱文件不能超过 2 MB');
        const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
        const state=await api('/api/import',{content:btoa(binary)});analyses=new Map();accept(state);await refreshLibrary();
    },'正在导入棋谱…');
};
$('exportGame').onclick=()=>{if(game)window.location.href=`/api/games/${game.id}/sgf`;};
$('refreshLibrary').onclick=()=>run(refreshLibrary);
$('pass').onclick=()=>run(async()=>{await mutate('move',{action:game.size*game.size});await continueAI();});
$('reviewPass').onclick=()=>run(()=>mutate('move',{action:game.size*game.size}));
$('undo').onclick=()=>run(()=>mutate('undo'));
$('retryAI').onclick=()=>run(continueAI);
$('resign').onclick=()=>{if(confirm('确认认输？本局棋谱仍会保留，可以继续复盘。'))run(async()=>{await mutate('resign');await refreshLibrary();});};
$('enterReview').onclick=()=>run(async()=>{await mutate('review');await refreshLibrary();});
$('analyze').onclick=()=>run(async()=>{
    const result=await api(`/api/games/${game.id}/analysis`,{node:game.cursor,visits:Number($('analysisVisits').value)});analyses.set(result.node,result);
},'KataGo 正在分析当前局面…');
$('showOwnership').onchange=drawBoard;
$('first').onclick=()=>goTo('root');
$('previous').onclick=()=>game.parent&&goTo(game.parent);
$('next').onclick=()=>game.children[0]&&goTo(game.children[0].id);
$('last').onclick=()=>goTo(game.route.at(-1));
$('mainline').onclick=()=>goTo('root');
$('timeline').onchange=()=>goTo(game.route[Number($('timeline').value)]);
$('previewScore').onclick=()=>run(()=>mutate('score',{dead:[...dead]}));
$('confirmScore').onclick=()=>run(async()=>{await mutate('score',{dead:[...dead],confirm:true});await refreshLibrary();});
$('resumeGame').onclick=()=>run(async()=>{await mutate('resume');await continueAI();});
$('pvBack').onclick=()=>{if(preview&&preview.index>0){preview.index--;render();}};
$('pvNext').onclick=()=>{if(preview&&preview.index<preview.frames.length-1){preview.index++;render();}};
$('pvClose').onclick=()=>{preview=null;render();};
$('scan').onclick=scan;
$('chart').onclick=event=>{if(!game||game.mode!=='review'||busy)return;const rect=$('chart').getBoundingClientRect();const index=Math.max(0,Math.min(game.route.length-1,Math.round(((event.clientX-rect.left)/rect.width*300-12)/276*(game.route.length-1))));goTo(game.route[index]);};
document.addEventListener('keydown',event=>{if(['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)||$('newDialog').open||$('studyDialog').open||busy)return;if(event.key==='ArrowLeft'){event.preventDefault();preview?$('pvBack').click():$('previous').click();}if(event.key==='ArrowRight'){event.preventDefault();preview?$('pvNext').click():$('next').click();}if(event.key==='Escape'&&preview){preview=null;render();}});
run(async()=>{
    const id=localStorage.getItem('go-study-active');
    if(id){try{await openGame(id);}catch(error){if(error.status!==404)throw error;localStorage.removeItem('go-study-active');await refreshLibrary();}}
    else await refreshLibrary();
});

let studySession = null, studyResult = null, studyChoice = null, studyFrame = -1;
let studyWorking = false, studyOnlyDue = true, studyStopped = false;
let studyLibraryData = null, studyTag = '', studySelectedTags = new Set(), studyTagInputs = [];
let studyLearningLoaded = false;
let studySavedNote = '', studySavedTags = '[]';
let comparisonData = null, comparisonStep = 0, comparisonOpen = false;

function mayLeaveStudy() {
    const dirty = studyResult && ($('studyNote').value !== studySavedNote ||
        (studyLearningLoaded && JSON.stringify([...studySelectedTags].sort()) !== studySavedTags));
    return !dirty || confirm('笔记或标签尚未保存，离开将放弃这些修改。是否继续？');
}

async function generateStudy(single = false) {
    await run(async () => {
        const id = game.id, revision = game.revision;
        studyStopped = false;
        $('stopStudy').hidden = false;
        $('stopStudy').disabled = false;
        $('studyColor').disabled = true;
        let saved = 0, existing = 0, checked = 0, insufficient = 0;
        try {
            const shortlist = single ? {nodes: [game.cursor]} : await api(`/api/games/${id}/study-shortlist`, {revision, color: Number($('studyColor').value)});
            if (!shortlist.nodes.length) {
                $('studyProgress').textContent = shortlist.covered < shortlist.total ? `初筛尚未完整（${shortlist.covered}/${shortlist.total}）。请先完成全谱初筛。` : '当前分支没有符合条件的明显损失，无需为了练习而制造错题。';
                return;
            }
            for (const node of shortlist.nodes) {
                if (studyStopped || saved >= 3) break;
                $('studyProgress').textContent = `正在复核 ${checked+1}/${shortlist.nodes.length} · 已新增 ${saved} 道 · 可停止后续复核`;
                try {
                    const result = await api(`/api/games/${id}/study-points`, {revision, node}, AbortSignal.timeout(210000));
                    if (result.saved) result.existing ? existing++ : saved++;
                    if (single && !result.saved) $('studyProgress').textContent = result.reason;
                } catch (error) {
                    if (error.status !== 422) throw error;
                    insufficient++;
                }
                checked++;
            }
            if (!single || saved || existing || insufficient) $('studyProgress').textContent = `${studyStopped ? '已停止' : '复核完成'} · 新增 ${saved} 道，已有 ${existing} 道${insufficient ? `，${insufficient} 个局面搜索不足` : ''}。从“学习中心”开始。${!single && shortlist.covered < shortlist.total ? '当前仅覆盖部分初筛结果。' : ''}`;
        } catch (error) {
            $('studyProgress').textContent = `复核中断，已新增 ${saved} 道，已有 ${existing} 道。已保存内容可直接练习。`;
            throw error;
        } finally {
            $('stopStudy').hidden = true;
            $('studyColor').disabled = false;
        }
    }, '正在复核学习点…');
}

async function studyRun(task) {
    if (studyWorking) return;
    studyWorking = true;
    $('studyError').textContent = '';
    renderStudy();
    try { await task(); }
    catch (error) { $('studyError').textContent = error.message || '连接失败，请确认本地服务仍在运行'; }
    finally { studyWorking = false; renderStudy(); }
}

async function refreshStudy() {
    const [data, stats] = await Promise.all([api('/api/study'), api(`/api/study/stats?days=${$('studyPeriod').value || '7'}`)]);
    studyLibraryData = data;
    $('studyTagFilter').replaceChildren();
    for (const [value, label] of [['', '全部知识点'], ['untagged', '尚未标注'], ...data.tag_options.map(tag => [tag, tag])]) {
        const option = document.createElement('option'); option.value = value; option.textContent = label;
        $('studyTagFilter').append(option);
    }
    $('studyTagFilter').value = studyTag;
    renderStudyStats(stats);
    renderStudyList();
}

function matchesStudyTag(point) {
    return !studyTag || (studyTag === 'untagged' ? !point.tags?.length : point.tags?.includes(studyTag));
}

function renderStudyList() {
    const data = studyLibraryData;
    if (!data) return;
    const now = Date.now();
    const points = data.points.filter(point => matchesStudyTag(point) && (!studyOnlyDue || (point.available !== false && Date.parse(point.due) <= now)));
    $('studySummary').textContent = `${data.points.length} 道练习 · ${data.due_count} 道待复习 · 已完成 ${data.completed} 次。答对后按 1、3、7 天安排复习。`;
    $('studyFilterNote').textContent = `${studyTag === 'untagged' ? '尚未标注' : studyTag || '全部知识点'} · ${studyOnlyDue ? '待复习' : '全部练习'} · ${points.length} 道。列表不受上方统计时段限制。`;
    $('studyList').replaceChildren();
    for (const point of points) {
        const button = document.createElement('button'); button.className = 'study-card';
        const title = document.createElement('strong'), detail = document.createElement('small');
        title.textContent = `${point.size} 路 · 第 ${point.step} 手 · ${point.player === 1 ? '黑' : '白'}先`;
        const verdict = {accepted:'上次通过', retry:'有待巩固', revealed:'上次看过答案'}[point.last_verdict] || '尚未练习';
        detail.textContent = point.available === false ? `${point.title} · 模型已变更，请回原棋谱重新收录` : `${point.title} · ${verdict} · ${Date.parse(point.due) <= now ? '待复习' : '下次 ' + new Date(point.due).toLocaleString('zh-CN')} · 连续通过 ${point.streak} 次`;
        const tags = document.createElement('small'); tags.textContent = point.tags?.length ? point.tags.join(' · ') : '尚未标注知识点';
        button.disabled = point.available === false;
        button.append(title, detail, tags);
        button.onclick = () => studyRun(() => startStudy(point.id));
        $('studyList').append(button);
    }
    if (!points.length) {
        const empty = document.createElement('p'); empty.className = 'muted small';
        empty.textContent = data.points.length ? '当前筛选下没有练习。可切换“全部练习”或“全部知识点”；旧模型练习在全部列表中保留。' : '还没有练习。进入复盘，完成全谱初筛，再点击“精选本分支”。';
        $('studyList').append(empty);
    }
}

async function startStudy(id) {
    studySession = await api(`/api/study/${id}/start`, {});
    studyResult = null; studyChoice = null; studyFrame = -1;
    resetStudyComparison();
    studyLearningLoaded = false; studySelectedTags = new Set(); studyTagInputs = [];
    $('studyTagChoices').replaceChildren(); $('studyHistory').replaceChildren();
    $('studyTagStatus').textContent = '';
    $('studyCoordinate').value = '';
    $('studyNote').value = '';
    studySavedNote = ''; studySavedTags = '[]';
    $('studyNoteStatus').textContent = '';
    $('studyLibrary').hidden = true;
    $('studyExercise').hidden = false;
}

function renderStudy() {
    ['closeStudy','studyDue','studyAll','studyBack','studySelect','studyPass','studyReveal','saveStudyNote','studySource','studyPeriod','studyTagFilter','refreshStudy','reloadStudyLearning','studyNextDue'].forEach(id => $(id).disabled = studyWorking);
    $('saveStudyTags').disabled = studyWorking || !studyLearningLoaded;
    studyTagInputs.forEach(input => input.disabled = studyWorking);
    $('studySubmit').disabled = studyWorking || studyChoice === null || !!studyResult;
    $('studyCoordinate').disabled = studyWorking;
    $('studyNote').disabled = studyWorking;
    $('studyDue').classList.toggle('primary', studyOnlyDue);
    $('studyAll').classList.toggle('primary', !studyOnlyDue);
    $('openStudyComparison').disabled = studyWorking || !studyResult;
    renderStudyComparison();
    if (!studySession) return;
    const s = studySession;
    $('studyPrompt').textContent = `第 ${s.step} 手，轮到${s.player === 1 ? '黑' : '白'}方 · ${s.size} 路 · 白贴 ${s.komi} 目 · ${s.rules === 'Chinese' ? '中国数子' : s.rules === 'Japanese' ? '日本规则' : '韩国规则'}`;
    $('studyInput').hidden = !!studyResult;
    $('studyFeedback').hidden = !studyResult;
    $('studySelection').textContent = studyChoice === null ? '点击棋盘或输入坐标选点' : `已选择 ${coordinate(studyChoice, s.size)}，提交后查看评价`;
    const frame = studyResult && studyFrame >= 0 ? {frames: studyResult.frames, index: studyFrame} : null;
    paintBoard($('studyBoard'), {...s, moves: [], status: 'playing'}, frame, null, new Set(), null);
    if (!frame && studyChoice !== null && studyChoice < s.size*s.size) {
        const ctx = $('studyBoard').getContext('2d'), cell = 688/(s.size-1);
        const x = 36 + (studyChoice%s.size)*cell, y = 36 + Math.floor(studyChoice/s.size)*cell;
        ctx.strokeStyle = '#b35037'; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(x, y, cell*.35, 0, Math.PI*2); ctx.stroke();
    }
    $('studyPvBack').disabled = studyWorking || studyFrame < 0;
    $('studyPvNext').disabled = studyWorking || !studyResult || studyFrame >= studyResult.frames.length-1;
    $('studyPvLabel').textContent = studyFrame < 0 ? '原始局面' : `推荐变化 ${studyFrame+1}/${studyResult.frames.length}`;
}

function chooseStudy(action) {
    if (!studySession || studyWorking || studyResult) return;
    if (!studySession.legal.includes(action)) {
        $('studyError').textContent = '这个落点不合法，请选择其他交叉点。'; return;
    }
    $('studyError').textContent = '';
    studyChoice = action;
    $('studyCoordinate').value = action === studySession.size**2 ? 'pass' : coordinate(action, studySession.size);
    renderStudy();
}

async function submitStudy(reveal = false) {
    if (!studySession || studyResult || (!reveal && studyChoice === null)) return;
    await studyRun(async () => {
        $('studySelection').textContent = '正在评估这一手，请稍候…';
        studyResult = await api(`/api/study-attempts/${studySession.attempt_id}/answer`, {action: reveal ? null : studyChoice});
        $('studyVerdict').textContent = {accepted:'通过 · 这手值得肯定', retry:'还可改进 · 对照推荐变化再想一想', revealed:'已看答案 · 本次不计通过'}[studyResult.verdict];
        const answer = studyResult.answer === 'pass' ? '停着' : studyResult.answer;
        $('studyComparison').textContent = `${answer ? `你的选择 ${answer}，预计损失 ${studyResult.loss.toFixed(2)} 目。` : ''}实战 ${studyResult.actual}，预计损失 ${studyResult.original_loss.toFixed(2)} 目；推荐 ${studyResult.best}。`;
        $('studyQuality').textContent = `预计损失以本题保存的推荐落点为参照；不超过 1 目视为通过。参考搜索 ${studyResult.reference_visits} 次${studyResult.answer_visits ? `，作答落点 ${studyResult.answer_visits} 次` : ''}。有限搜索有误差，结果不等于棋理定论。`;
        $('studySchedule').textContent = `下次复习：${new Date(studyResult.due).toLocaleString('zh-CN')}。提前答对不会延后已有安排；未通过或看答案，10 分钟后再试。`;
        $('studyNote').value = studyResult.note;
        studySavedNote = studyResult.note;
        await loadStudyLearning();
    });
}

$('generateStudy').onclick = () => generateStudy();
$('saveStudy').onclick = () => generateStudy(true);
$('stopStudy').onclick = () => {studyStopped = true; $('stopStudy').disabled = true; $('studyProgress').textContent = '正在停止：当前复核完成后不再提交下一局面。';};
$('openStudy').onclick = () => {
    stopScan(); render();
    studySession = null; studyResult = null;
    resetStudyComparison();
    $('studyLibrary').hidden = false; $('studyExercise').hidden = true;
    $('studyDialog').showModal();
    studyRun(refreshStudy);
};
$('closeStudy').onclick = () => {if(mayLeaveStudy()) $('studyDialog').close();};
$('studyDialog').addEventListener('cancel', event => {if (studyWorking || !mayLeaveStudy()) event.preventDefault();});
$('studyDue').onclick = () => studyRun(async () => {studyOnlyDue = true; await refreshStudy();});
$('studyAll').onclick = () => studyRun(async () => {studyOnlyDue = false; await refreshStudy();});
$('studyBack').onclick = () => {if(mayLeaveStudy()) return studyRun(async () => {await refreshStudy(); studySession = null; studyResult = null; $('studyLibrary').hidden = false; $('studyExercise').hidden = true;});};
$('studyBoard').addEventListener('click', event => {
    if (!studySession) return;
    const rect = $('studyBoard').getBoundingClientRect(), size = studySession.size, cell = 688/(size-1);
    const c = Math.round(((event.clientX-rect.left)*760/rect.width-36)/cell), r = Math.round(((event.clientY-rect.top)*760/rect.height-36)/cell);
    if (c>=0 && c<size && r>=0 && r<size) chooseStudy(r*size+c);
});
$('studySelect').onclick = () => {
    if (!studySession) return;
    const value = $('studyCoordinate').value.trim().toUpperCase(), size = studySession.size;
    if (value === 'PASS') {chooseStudy(size*size); return;}
    if (!/^[A-HJ-T][1-9][0-9]?$/.test(value) || COLS.indexOf(value[0])>=size || Number(value.slice(1))>size) {
        $('studyError').textContent = '请输入棋盘范围内的坐标，如 D4（列号跳过 I）。'; return;
    }
    chooseStudy(toAction(value, size));
};
$('studyCoordinate').addEventListener('keydown', event => {if(event.key === 'Enter'){event.preventDefault();$('studySelect').click();}});
$('studyPass').onclick = () => studySession && chooseStudy(studySession.size**2);
$('studySubmit').onclick = () => submitStudy();
$('studyReveal').onclick = () => submitStudy(true);
$('studyPvBack').onclick = () => {if(studyFrame>=0){studyFrame--;renderStudy();}};
$('studyPvNext').onclick = () => {if(studyResult && studyFrame<studyResult.frames.length-1){studyFrame++;renderStudy();}};
$('saveStudyNote').onclick = () => studyRun(async () => {await api(`/api/study/${studySession.point_id}/note`, {note:$('studyNote').value});studySavedNote = $('studyNote').value;$('studyNoteStatus').textContent = ' 已保存';});
$('studyNote').addEventListener('input', () => $('studyNoteStatus').textContent = ' 未保存');
$('studySource').onclick = () => {if(!mayLeaveStudy()) return; return studyRun(async () => {
    const source = studyResult.source;
    await openGame(source.game_id);
    if(game.mode !== 'review') await mutate('review');
    await mutate('navigate', {node:source.node});
    $('studyDialog').close();
});};

function renderStudyStats(data) {
    const summary = data.summary;
    $('studyMetrics').replaceChildren();
    for (const [label, value] of [['已作答', `${summary.answered} 次`], ['作答通过率', summary.pass_rate === null ? '暂无作答' : `${summary.pass_rate}%`], ['练过不同题目', `${summary.practiced_points} 道`], ['有练习的天数', `${summary.active_days} 天`]]) {
        const box = document.createElement('div'), name = document.createElement('span'), metric = document.createElement('strong');
        name.textContent = label; metric.textContent = value; box.append(name, metric); $('studyMetrics').append(box);
    }
    $('studyStatsNote').textContent = `通过 ${summary.accepted} 次 · 未通过 ${summary.retry} 次 · 直接看答案 ${summary.revealed} 次（不计入通过率）。按北京时间统计，包含旧模型的历史记录；这不是棋力评级。${summary.estimated_times ? ` 其中 ${summary.estimated_times} 条旧记录用练习开始时间近似归日。` : ''}`;
    $('studyTrendTitle').textContent = `每日练习记录 · 最近 ${data.daily.length} 天（北京时间）`;
    $('studyDaily').replaceChildren();
    const max = Math.max(1, ...data.daily.map(day => day.accepted + day.retry + day.revealed));
    data.daily.forEach(day => {
        const row = document.createElement('div'); row.className = 'study-day';
        const date = document.createElement('span'); date.textContent = day.day.slice(5);
        const bar = document.createElement('div'); bar.className = 'study-day-bar';
        for (const verdict of ['accepted','retry','revealed']) {
            const part = document.createElement('span'); part.className = `study-day-${verdict}`;
            part.style.width = `${day[verdict]/max*100}%`; bar.append(part);
        }
        const count = document.createElement('small'); count.textContent = `通过 ${day.accepted} · 未通过 ${day.retry} · 看答案 ${day.revealed}`;
        row.append(date, bar, count); $('studyDaily').append(row);
    });
    $('studyTopics').replaceChildren();
    data.topics.forEach(topic => {
        const button = document.createElement('button'); button.className = 'study-topic';
        const title = document.createElement('strong'), detail = document.createElement('small');
        title.textContent = `${topic.tag} · ${topic.needs_work}/${topic.sample_count} 道待巩固`;
        detail.textContent = !topic.sufficient ? '样本不足 3 道，暂不判断薄弱点 · 查看这一类 →' : topic.needs_work ? '最近结果未通过或看过答案 · 练习这一类 →' : '本时段最近结果均已通过 · 重温这一类 →';
        button.append(title, detail);
        button.onclick = () => {
            if (studyWorking) return;
            studyTag = topic.tag; studyOnlyDue = false; $('studyTagFilter').value = studyTag;
            renderStudyList(); renderStudy(); $('studyFilterNote').scrollIntoView?.({block:'nearest',behavior:'smooth'});
        };
        $('studyTopics').append(button);
    });
    if (!data.topics.length) $('studyTopics').textContent = '还没有已标注的知识点。完成练习后，选择标签以积累回顾依据。';
    $('studyTopicNote').textContent = `每题只取所选时段内最近一次结果，按当前手动标签归类，仅纳入当前模型可用练习。至少 3 道不同题目才提供回顾依据；多标签题会分别计入各类。另有 ${data.untagged_count} 道可用练习尚未标注。`;
}

async function loadStudyLearning() {
    const data = await api(`/api/study/${studySession.point_id}/learning`);
    studySelectedTags = new Set(data.tags);
    studySavedTags = JSON.stringify([...studySelectedTags].sort());
    studyLearningLoaded = true;
    studyTagInputs = [];
    $('studyTagStatus').textContent = '';
    $('studyTagChoices').replaceChildren();
    data.tag_options.forEach(tag => {
        const label = document.createElement('label'), input = document.createElement('input'), name = document.createElement('span');
        input.type = 'checkbox'; input.checked = studySelectedTags.has(tag); name.textContent = tag;
        input.onchange = () => {
            if (input.checked && studySelectedTags.size >= 3) {
                input.checked = false; $('studyTagStatus').textContent = ' 最多选择 3 个'; return;
            }
            input.checked ? studySelectedTags.add(tag) : studySelectedTags.delete(tag);
            $('studyTagStatus').textContent = ' 未保存';
        };
        label.append(input, name); $('studyTagChoices').append(label); studyTagInputs.push(input);
    });
    $('studyHistory').replaceChildren();
    data.history.forEach(item => {
        const row = document.createElement('li');
        const verdict = {accepted:'通过',retry:'未通过',revealed:'看过答案'}[item.verdict];
        row.textContent = `${new Date(item.completed_at).toLocaleString('zh-CN')} · ${verdict}${item.answer ? ` · ${item.answer === 'pass' ? '停着' : item.answer} · 预计损失 ${item.loss.toFixed(2)} 目` : ''}${item.time_estimated ? '（旧记录，时间近似）' : ''}`;
        $('studyHistory').append(row);
    });
}

$('studyPeriod').onchange = () => studyRun(refreshStudy);
$('refreshStudy').onclick = () => studyRun(refreshStudy);
$('studyTagFilter').onchange = () => {studyTag = $('studyTagFilter').value; renderStudyList();};
$('reloadStudyLearning').onclick = () => studyRun(loadStudyLearning);
$('saveStudyTags').onclick = () => studyRun(async () => {
    await api(`/api/study/${studySession.point_id}/tags`, {tags:[...studySelectedTags]});
    studySavedTags = JSON.stringify([...studySelectedTags].sort());
    $('studyTagStatus').textContent = ' 已保存';
});
$('studyNextDue').onclick = () => {if(!mayLeaveStudy()) return; return studyRun(async () => {
    const data = await api('/api/study');
    const next = data.points.find(point => point.id !== studySession.point_id && point.available !== false && Date.parse(point.due) <= Date.now() && matchesStudyTag(point));
    if (next) await startStudy(next.id);
    else {
        await refreshStudy(); studySession = null; studyResult = null;
        $('studyLibrary').hidden = false; $('studyExercise').hidden = true;
        $('studyFilterNote').textContent = '当前知识点没有其他到期练习。可以稍后再来，或切换“全部练习”提前重温。';
    }
});};

function resetStudyComparison() {
    comparisonData = null; comparisonStep = 0; comparisonOpen = false;
    $('comparisonActualMode').value = 'recorded';
    $('comparisonPanel').hidden = true;
}

function renderStudyComparison() {
    $('comparisonPanel').hidden = !comparisonOpen || !comparisonData || !studyResult;
    $('openStudyComparison').textContent = studyWorking && !comparisonData ? '对照将在当前操作完成后可用' : '对照实战、重下与推荐变化';
    if (!comparisonData || !comparisonOpen || !studyResult) return;
    const data = comparisonData, actualMode = $('comparisonActualMode').value || 'recorded';
    const lines = {Actual:data.lines[actualMode], Answer:data.lines.answer, Recommended:data.lines.recommended};
    const maximum = Math.max(...Object.values(lines).filter(Boolean).map(line => line.frames.length-1));
    comparisonStep = Math.min(comparisonStep, maximum);
    $('comparisonTimeline').max = maximum; $('comparisonTimeline').value = comparisonStep;
    $('comparisonTimeline').disabled = studyWorking || maximum === 0;
    $('comparisonFirst').disabled = $('comparisonBack').disabled = studyWorking || comparisonStep === 0;
    $('comparisonNext').disabled = studyWorking || comparisonStep === maximum;
    ['comparisonActualMode','comparisonDifferences','closeStudyComparison'].forEach(id => $(id).disabled = studyWorking);
    $('comparisonMeta').textContent = `从第 ${data.step} 手落子前开始 · ${data.size} 路 · 白贴 ${data.komi} 目 · ${data.rules === 'Chinese' ? '中国数子' : data.rules === 'Japanese' ? '日本规则' : '韩国规则'}`;
    $('comparisonPosition').textContent = comparisonStep === 0 ? '共同起点 · 还没有落下这一手' : `回放第 ${comparisonStep} 手 · 较短的变化停在自己的末尾`;
    const reference = lines.Recommended.frames[Math.min(comparisonStep, lines.Recommended.frames.length-1)];
    for (const [name, line] of Object.entries(lines)) {
        const boardCanvas = $(`comparison${name}Board`);
        boardCanvas.hidden = !line;
        $(`comparison${name}Warning`).textContent = line?.warning || '';
        if (!line) {
            $(`comparison${name}Metric`).textContent = '本次直接看了答案，没有提交重下落点。';
            $(`comparison${name}Position`).textContent = '重新打开题目并作答后，可对照自己的变化。';
            continue;
        }
        const index = Math.min(comparisonStep, line.frames.length-1), frame = line.frames[index];
        paintBoard(boardCanvas, {size:data.size,board:frame.board,moves:[],player:frame.player,status:'playing',legal:[]}, {frames:line.frames,index}, null, new Set(), null);
        let differences = 0;
        for (let r=0; r<data.size; r++) for (let c=0; c<data.size; c++) {
            if (frame.board[r][c] === reference.board[r][c]) continue;
            differences++;
            if ($('comparisonDifferences').checked) {
                const ctx=boardCanvas.getContext('2d'), cell=688/(data.size-1);
                ctx.strokeStyle='#b35c2a';ctx.lineWidth=3;
                ctx.beginPath();ctx.arc(36+c*cell,36+r*cell,cell*.47,0,Math.PI*2);ctx.stroke();
            }
        }
        const loss = name === 'Actual' ? data.original_loss : name === 'Answer' ? data.answer_loss : 0;
        $(`comparison${name}Metric`).textContent = name === 'Actual' && actualMode === 'recorded'
            ? `收录时的棋谱分支 · 首手预计损失 ${loss.toFixed(2)} 目`
            : `${name === 'Answer' && data.answer_recomputed ? '旧作答补算 · ' : ''}首手评估：黑方 ${line.black_lead >= 0 ? '+' : ''}${line.black_lead.toFixed(2)} 目 · ${line.visits} 次搜索${name === 'Answer' && data.answer_recomputed ? '' : ` · 首手预计损失 ${loss.toFixed(2)} 目`}`;
        const last = index ? `${(data.player * (index%2 ? 1 : -1)) === 1 ? '黑' : '白'} ${line.moves[index-1] === 'pass' ? '停着' : line.moves[index-1]}` : '落子前';
        $(`comparison${name}Position`).textContent = `${index}/${line.moves.length} 手 · ${last}${index === line.moves.length ? ' · 已到本线末尾' : ''} · 黑提 ${frame.captures.black}，白提 ${frame.captures.white}${name !== 'Recommended' ? ` · 与推荐栏当前画面有 ${differences} 处不同` : ''}`;
    }
    $('comparisonCaveat').textContent = data.answer_recomputed
        ? '这条旧作答未保存变化，本次补算并缓存。新目差可能与当时的判分不同；原判分、作答次数和复习时间均保留。'
        : '推演使用收录或作答时保存的分析，最多回放 12 手；它不保证唯一正确，也不等于真实后续。提子数从本次共同起点累计。';
}

$('openStudyComparison').onclick = () => {
    if (!studySession || !studyResult) return;
    return studyRun(async () => {
        if (!comparisonData) comparisonData = await api(`/api/study-attempts/${studySession.attempt_id}/comparison`, {});
        comparisonOpen = true;
        renderStudyComparison();
        $('comparisonPanel').scrollIntoView?.({block:'start',behavior:'smooth'});
    });
};
$('closeStudyComparison').onclick = () => {comparisonOpen=false;renderStudyComparison();};
$('comparisonFirst').onclick = () => {comparisonStep=0;renderStudyComparison();};
$('comparisonBack').onclick = () => {comparisonStep=Math.max(0,comparisonStep-1);renderStudyComparison();};
$('comparisonNext').onclick = () => {comparisonStep++;renderStudyComparison();};
$('comparisonTimeline').oninput = () => {comparisonStep=Number($('comparisonTimeline').value);renderStudyComparison();};
$('comparisonActualMode').onchange = renderStudyComparison;
$('comparisonDifferences').onchange = renderStudyComparison;
