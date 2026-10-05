// Client unit tests with a synthetic DOM and mocked requests; no browser or network.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const elements = new Map();
const noop = () => {};
class Element {
    constructor(id='') { Object.assign(this, {id, children:[], events:{}, style:{}, value:'', textContent:'', hidden:false, disabled:false, open:false, checked:false, files:[], width:760, classList:{toggle:noop}}); }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, handler) { this.events[name] = handler; }
    showModal() { this.open=true; }
    close() { this.open=false; }
    getBoundingClientRect() { return {left:0,top:0,width:760,height:760}; }
    getContext() { return new Proxy({}, {get:(_,key)=>key.startsWith('create')?()=>({addColorStop:noop}):noop,set:()=>true}); }
    click() { if (!this.disabled) return this.onclick?.(); }
}
for (const match of html.matchAll(/id="([^"]+)"/g)) {
    assert(!elements.has(match[1]), 'duplicate element id');
    elements.set(match[1], new Element(match[1]));
}
elements.get('aiVisits').value='200';
elements.get('analysisVisits').value='400';
let handler = async () => [];
const context = vm.createContext({console, setTimeout, clearTimeout, AbortSignal,
    document:{getElementById:id=>{assert(elements.has(id), `missing element ${id}`);return elements.get(id);},
        createElement:()=>new Element(), querySelectorAll:()=>elements.get('library').children,
        body:new Element(), addEventListener:noop},
    localStorage:{getItem:()=>null,setItem:noop,removeItem:noop}, window:{location:{}}, confirm:()=>true,
    fetch:async (url, options)=>{const result=await handler(url,options);return {ok:true,json:async()=>result};}
});
vm.runInContext(source, context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const fixture = () => ({id:'test',size:9,komi:7.5,rules:'Chinese',board:Array.from({length:9},()=>Array(9).fill(0)),
    player:1,passes:0,moves:[],cursor:'root',parent:null,children:[],route:['root'],step:0,revision:0,
    mode:'play',human_color:1,status:'playing',handicap_left:0,dead:[],score:null,
    legal:Array.from({length:82},(_,i)=>i),comment:'',black_name:'棋友',white_name:'KataGo',result:''});
function setGame(state) {context.fixture=state;vm.runInContext('accept(fixture)',context);}

(async()=>{
    await tick();
    assert.equal(elements.get('analyze').disabled,true);
    assert.equal(elements.get('newGame').disabled,false);
    console.log('PASS empty state and all referenced element ids');

    setGame(fixture());
    assert.equal(elements.get('pass').disabled,false);
    const next=fixture();next.player=-1;next.step=1;next.moves=[['B','A9']];next.cursor='root/0';next.route=['root','root/0'];next.revision=1;next.board[0][0]=1;
    const afterAI=structuredClone(next);afterAI.board[2][2]=-1;afterAI.player=1;afterAI.step=2;afterAI.revision=2;
    const calls=[];
    handler=async(url,options)=>{calls.push({url,body:options.body&&JSON.parse(options.body)});if(url.endsWith('/move'))return next;if(url.endsWith('/ai'))return afterAI;return [];};
    await vm.runInContext('run(async()=>{await mutate("move",{action:0});await continueAI();})',context);
    assert.equal(calls.length,2);
    assert.equal(calls[1].body.revision,1);
    assert.equal(vm.runInContext('game.step',context),2);
    assert.equal(elements.get('pass').disabled,false);
    console.log('PASS human move followed by AI with the new revision');

    setGame(next);
    handler=async()=>{throw new Error('测试引擎离线');};
    await vm.runInContext('run(continueAI)',context);
    assert.equal(vm.runInContext('game.step',context),1);
    assert.equal(elements.get('retryAI').hidden,false);
    assert.equal(elements.get('retryAI').disabled,false);
    assert.equal(elements.get('notice').textContent,'测试引擎离线');
    console.log('PASS failed AI preserves the move and exposes retry');

    const review=fixture();review.mode='review';review.comment='<script>untrusted</script>';
    setGame(review);
    assert.equal(elements.get('reviewControls').hidden,false);
    assert.equal(elements.get('playControls').hidden,true);
    assert.equal(elements.get('comment').textContent,review.comment);
    assert.equal(elements.get('reviewPass').disabled,false);
    console.log('PASS review controls and plain-text SGF comments');

    const scoring=fixture();scoring.status='scoring';scoring.score={black:0,white:7.5,result:'W+7.5',territory:Array(81).fill(0)};
    setGame(scoring);
    assert.equal(elements.get('scoringPanel').hidden,false);
    assert.equal(elements.get('pass').disabled,true);
    assert.equal(elements.get('confirmScore').disabled,false);
    console.log('PASS scoring locks normal play and exposes confirmation');

    vm.runInContext('preview={frames:[{board:game.board,action:81},{board:game.board,action:81}],index:0};render()',context);
    assert.equal(elements.get('pvBack').disabled,true);
    elements.get('pvNext').click();
    assert.equal(elements.get('pvNext').disabled,true);
    elements.get('pvClose').click();
    assert.equal(elements.get('previewBar').hidden,true);
    console.log('PASS variation next, previous boundary and return');

    const studySession = {attempt_id:'attempt',point_id:'point',size:9,komi:7.5,rules:'Chinese',player:1,step:1,
        board:fixture().board,legal:Array.from({length:82},(_,i)=>i)};
    const learning = {tags:[],tag_options:['死活','官子','手筋','劫争'],note:'',history:[]};
    handler=async(url)=>url.endsWith('/start')?studySession:{points:[],due_count:0,completed:0};
    const before=vm.runInContext('JSON.stringify(game)',context);
    await vm.runInContext('studyRun(()=>startStudy("point"))',context);
    assert.equal(elements.get('studyFeedback').hidden,true);
    assert.equal(elements.get('studyInput').hidden,false);
    assert.equal(elements.get('studySubmit').disabled,true);
    elements.get('studyCoordinate').value='I4';elements.get('studySelect').click();
    assert.match(elements.get('studyError').textContent,/请输入/);
    elements.get('studyCoordinate').value='D6';elements.get('studySelect').click();
    assert.equal(vm.runInContext('studyChoice',context),30);
    assert.equal(elements.get('studySubmit').disabled,false);
    assert.equal(vm.runInContext('JSON.stringify(game)',context),before);
    console.log('PASS independent practice, hidden answers and coordinate validation');

    handler=async()=>{throw new Error('练习分析离线');};
    await elements.get('studySubmit').click();
    assert.equal(vm.runInContext('studyChoice',context),30);
    assert.equal(elements.get('studySubmit').disabled,false);
    assert.equal(elements.get('studyFeedback').hidden,true);
    assert.equal(elements.get('studyError').textContent,'练习分析离线');
    console.log('PASS failed practice search retains selection and allows retry');

    const feedback={verdict:'accepted',answer:'D6',loss:.5,actual:'A9',original_loss:5,best:'C7',frames:[{board:fixture().board,action:20}],
        reference_visits:399,answer_visits:399,due:'2026-10-03T08:00:00+00:00',streak:1,note:'<script>笔记</script>',point_id:'point',source:{game_id:'test',node:'root'}};
    let answers=0;
    handler=async(url,options)=>{if(url.endsWith('/answer')){answers++;assert.equal(JSON.parse(options.body).action,30);return feedback;}if(url.endsWith('/learning'))return learning;return {saved:true};};
    await elements.get('studySubmit').click();
    assert.equal(elements.get('studyFeedback').hidden,false);
    assert.equal(elements.get('studyInput').hidden,true);
    assert.equal(elements.get('studyNote').value,feedback.note);
    assert.equal(elements.get('studyError').textContent,'');
    await vm.runInContext('submitStudy()',context);
    assert.equal(answers,1);
    elements.get('studyPvNext').click();
    assert.equal(elements.get('studyPvNext').disabled,true);
    elements.get('studyPvBack').click();
    assert.equal(elements.get('studyPvBack').disabled,true);
    await elements.get('saveStudyNote').click();
    assert.match(elements.get('studyNoteStatus').textContent,/已保存/);
    assert.equal(vm.runInContext('JSON.stringify(game)',context),before);
    console.log('PASS feedback, duplicate-submit guard, PV bounds and note save');

    handler=async()=>studySession;
    await vm.runInContext('studyRun(()=>startStudy("point"))',context);
    handler=async(url,options)=>{if(url.endsWith('/learning'))return learning;assert.equal(JSON.parse(options.body).action,null);return {...feedback,verdict:'revealed',answer:null,loss:null};};
    await elements.get('studyReveal').click();
    assert.match(elements.get('studyVerdict').textContent,/不计通过/);
    console.log('PASS explicit reveal is separate from a move submission');

    setGame(review);
    elements.get('studyColor').value='0';
    const generated=[];
    handler=async(url,options)=>{
        if(url.endsWith('/study-shortlist'))return {nodes:['root/0','root/0/0'],covered:2,total:2};
        if(url.endsWith('/study-points')){generated.push(JSON.parse(options.body));elements.get('stopStudy').click();return {saved:true,existing:false};}
        return [];
    };
    await vm.runInContext('generateStudy()',context);
    assert.equal(generated.length,1);
    assert.match(elements.get('studyProgress').textContent,/已停止.*新增 1/);
    assert.equal(elements.get('stopStudy').hidden,true);
    assert.equal(elements.get('generateStudy').disabled,false);
    console.log('PASS stopping selection preserves completed cards and skips next request');

    let reviewed=0;
    handler=async(url)=>{
        if(url.endsWith('/study-shortlist'))return {nodes:['a','b','c','d','e','f'],covered:6,total:6};
        reviewed++;return {saved:true,existing:reviewed<=2};
    };
    await vm.runInContext('generateStudy()',context);
    assert.equal(reviewed,5);
    assert.match(elements.get('studyProgress').textContent,/新增 3 道，已有 2 道/);
    console.log('PASS existing cards do not prevent up to three new selections');

    const point = (id,tags,available=true) => ({id,tags,available,size:9,step:1,player:1,title:'<img src=x onerror=alert(1)>',due:'2000-01-01T00:00:00Z',streak:0,last_verdict:'retry'});
    const library = {points:[point('point',['死活']),point('other',['官子']),point('next',['死活']),point('blank',[]),point('old',['死活'],false)],due_count:4,completed:3,tag_options:learning.tag_options};
    const stats = {summary:{answered:2,accepted:1,retry:1,revealed:1,pass_rate:50,practiced_points:3,active_days:1,estimated_times:1},
        daily:[{day:'2026-10-03',accepted:1,retry:1,revealed:1}],topics:[{tag:'死活',sample_count:1,needs_work:1,sufficient:false,card_count:2}],untagged_count:1};
    handler=async(url)=>url.includes('/stats?')?stats:library;
    await vm.runInContext('studyRun(refreshStudy)',context);
    assert.equal(elements.get('studyMetrics').children[1].children[1].textContent,'50%');
    assert.match(elements.get('studyStatsNote').textContent,/直接看答案 1 次（不计入通过率）/);
    assert.match(elements.get('studyStatsNote').textContent,/旧记录/);
    assert.match(elements.get('studyTopics').children[0].children[1].textContent,/样本不足/);
    assert.equal(elements.get('studyList').children.length,4);
    assert.match(elements.get('studyList').children[0].children[1].textContent,/<img/);
    console.log('PASS learning dashboard, honest sample labels and plain-text titles');

    elements.get('studyTopics').children[0].click();
    assert.equal(vm.runInContext('studyTag',context),'死活');
    assert.equal(vm.runInContext('studyOnlyDue',context),false);
    assert.equal(elements.get('studyList').children.length,3);
    assert.equal(elements.get('studyList').children[2].disabled,true);
    elements.get('studyTagFilter').value='untagged';elements.get('studyTagFilter').onchange();
    assert.equal(elements.get('studyList').children.length,1);
    assert.match(elements.get('studyList').children[0].children[2].textContent,/尚未标注/);
    console.log('PASS topic-to-practice filtering, untagged cards and old-model exclusion');

    handler=async()=>({...learning,tags:['死活','官子','手筋'],history:[{verdict:'revealed',answer:null,loss:null,completed_at:'2026-10-03T00:00:00Z',time_estimated:true}]});
    await vm.runInContext('studyRun(loadStudyLearning)',context);
    vm.runInContext('studyTagInputs[3].checked=true;studyTagInputs[3].onchange()',context);
    assert.equal(vm.runInContext('studySelectedTags.size',context),3);
    assert.equal(vm.runInContext('studyTagInputs[3].checked',context),false);
    assert.match(elements.get('studyHistory').children[0].textContent,/看过答案.*旧记录/);
    handler=async()=>{throw new Error('保存失败');};
    await elements.get('saveStudyTags').click();
    assert.equal(vm.runInContext('studySelectedTags.size',context),3);
    handler=async(url,options)=>{assert.equal(JSON.parse(options.body).tags.length,3);return {tags:['死活','官子','手筋']};};
    await elements.get('saveStudyTags').click();
    assert.match(elements.get('studyTagStatus').textContent,/已保存/);
    console.log('PASS manual tag limit, retryable save and per-card history');

    vm.runInContext('studyTag="死活"',context);
    let started;
    handler=async(url)=>{
        if(url.endsWith('/start')){started=url;return {...studySession,point_id:'next'};}
        return library;
    };
    await elements.get('studyNextDue').click();
    assert.equal(started,'/api/study/next/start');
    assert.equal(elements.get('studyFeedback').hidden,true);
    assert.equal(elements.get('studyTagChoices').children.length,0);
    assert.equal(elements.get('studyHistory').children.length,0);
    console.log('PASS next due card respects selected topic and clears previous hints');

    handler=async(url)=>{if(url.endsWith('/answer'))return feedback;throw new Error('记录暂不可用');};
    await elements.get('studyReveal').click();
    assert.equal(elements.get('studyFeedback').hidden,false);
    assert.equal(elements.get('saveStudyTags').disabled,true);
    handler=async()=>learning;
    await elements.get('reloadStudyLearning').click();
    assert.equal(elements.get('saveStudyTags').disabled,false);
    assert.equal(elements.get('studyError').textContent,'');
    console.log('PASS metadata failure does not lose a graded result and can be retried');

    elements.get('studyDialog').showModal();
    elements.get('studyNote').value='还有一条未保存的思考';
    context.confirm=()=>false;
    elements.get('closeStudy').click();
    assert.equal(elements.get('studyDialog').open,true);
    let leaveRequests=0;
    handler=async()=>{leaveRequests++;return library;};
    await elements.get('studyNextDue').click();
    assert.equal(leaveRequests,0);
    context.confirm=()=>true;
    elements.get('closeStudy').click();
    assert.equal(elements.get('studyDialog').open,false);
    console.log('PASS unsaved notes or tags are protected when leaving or moving on');

    const replay = moves => ({moves,warning:'',visits:399,black_lead:5,frames:[{board:fixture().board,action:null,player:1,captures:{black:0,white:0}},...moves.map((move,i)=>({board:fixture().board,action:move==='pass'?81:20,player:i%2?1:-1,captures:{black:i+1,white:0}}))]});
    const comparison = {size:9,komi:7.5,rules:'Chinese',player:1,step:1,original_loss:5,answer_loss:.5,answer_recomputed:false,
        lines:{recorded:replay(['A9']),actual:replay(['A9','pass']),answer:replay(['D6','E5']),recommended:replay(['C7','E5','F4'])}};
    let compareCalls=0;
    handler=async(url)=>{assert(url.endsWith('/comparison'));compareCalls++;return comparison;};
    const sourceBefore=vm.runInContext('JSON.stringify(game)',context);
    const resultBefore=vm.runInContext('JSON.stringify(studyResult)',context);
    await elements.get('openStudyComparison').click();
    assert.equal(elements.get('comparisonPanel').hidden,false);
    assert.equal(elements.get('comparisonTimeline').max,3);
    assert.equal(elements.get('comparisonBack').disabled,true);
    elements.get('comparisonNext').click();elements.get('comparisonNext').click();
    assert.match(elements.get('comparisonActualPosition').textContent,/1\/1 手.*已到本线末尾/);
    assert.match(elements.get('comparisonAnswerPosition').textContent,/2\/2 手/);
    assert.match(elements.get('comparisonRecommendedPosition').textContent,/2\/3 手/);
    elements.get('comparisonNext').click();
    assert.equal(elements.get('comparisonNext').disabled,true);
    elements.get('comparisonActualMode').value='actual';elements.get('comparisonActualMode').onchange();
    assert.match(elements.get('comparisonActualPosition').textContent,/白 停着/);
    assert.match(elements.get('comparisonActualMetric').textContent,/首手评估/);
    elements.get('comparisonFirst').click();
    assert.equal(elements.get('comparisonTimeline').value,0);
    assert.equal(vm.runInContext('JSON.stringify(game)',context),sourceBefore);
    assert.equal(vm.runInContext('JSON.stringify(studyResult)',context),resultBefore);
    elements.get('closeStudyComparison').click();
    await elements.get('openStudyComparison').click();
    assert.equal(compareCalls,1);
    console.log('PASS synchronized comparison, short lines, pass, source switch and cached reopen');

    const captured = replay(['C7']);
    captured.frames[1].board=fixture().board;captured.frames[1].board[2][2]=1;
    context.changedComparison={...comparison,lines:{...comparison.lines,answer:null,recommended:captured},answer_loss:null,answer_recomputed:true};
    vm.runInContext('comparisonData=changedComparison;comparisonStep=1;renderStudyComparison()',context);
    assert.equal(elements.get('comparisonAnswerBoard').hidden,true);
    assert.match(elements.get('comparisonAnswerMetric').textContent,/没有提交/);
    assert.match(elements.get('comparisonActualPosition').textContent,/1 处不同/);
    assert.match(elements.get('comparisonCaveat').textContent,/原判分.*保留/);
    elements.get('comparisonDifferences').checked=false;elements.get('comparisonDifferences').onchange();
    assert.match(elements.get('comparisonActualPosition').textContent,/1 处不同/);
    console.log('PASS revealed comparison does not invent an answer and differences are counted');

    handler=async()=>studySession;
    await vm.runInContext('studyRun(()=>startStudy("point"))',context);
    assert.equal(elements.get('comparisonPanel').hidden,true);
    assert.equal(elements.get('openStudyComparison').disabled,true);
    let forbiddenCalls=0;handler=async()=>{forbiddenCalls++;return comparison;};
    await vm.runInContext('$("openStudyComparison").onclick()',context);
    assert.equal(forbiddenCalls,0);
    context.savedResult=feedback;vm.runInContext('studyResult=savedResult;renderStudy()',context);
    handler=async()=>{throw new Error('对照暂不可用');};
    await elements.get('openStudyComparison').click();
    assert.equal(elements.get('comparisonPanel').hidden,true);
    assert.equal(elements.get('openStudyComparison').disabled,false);
    assert.equal(elements.get('studyError').textContent,'对照暂不可用');
    handler=async()=>comparison;
    await elements.get('openStudyComparison').click();
    assert.equal(elements.get('comparisonPanel').hidden,false);
    console.log('PASS new question clears comparison, hides hints and allows retry after failure');
})().catch(error=>{console.error(error);process.exitCode=1;});
