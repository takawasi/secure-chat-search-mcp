"use strict";
const $ = (id) => document.getElementById(id);
let token = "", config, currentSubject = "aoki", requestGeneration = 0, readerGeneration = 0;
let selectedMessage = "", lastResults = [];
function node(tag, text, className) {
  const el = document.createElement(tag); if (text !== undefined) el.textContent = text;
  if (className) el.className = className; return el;
}
function date(s, full = false) {
  if (!s) return "未確認";
  return new Intl.DateTimeFormat("ja-JP", {timeZone:"Asia/Tokyo", year:"numeric",month:"2-digit",day:"2-digit",...(full?{hour:"2-digit",minute:"2-digit"}:{})}).format(new Date(s));
}
async function api(path, body, withAuth = true) {
  const headers = {}; if (withAuth && token) headers.Authorization = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {method:body===undefined?"GET":"POST",headers,body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error?.message || "入力または接続を確認してください。");
  return data;
}
function showError(error) { $("error").textContent = error.message || String(error); $("error").hidden = false; }
function clearError() { $("error").hidden = true; }
function clearPrivateView() {
  requestGeneration++; readerGeneration++; lastResults = []; selectedMessage = "";
  $("results").replaceChildren(); $("reader-content").replaceChildren(); $("scopes").replaceChildren();
  $("room-count").textContent="—"; $("message-count").textContent="—"; $("period").textContent="—";
  $("result-count").textContent="0"; $("warnings").replaceChildren();
}
async function refreshScope() {
  const generation = requestGeneration;
  const data = await api("/api/me");
  if (generation !== requestGeneration) return;
  $("room-count").textContent=data.rooms.length; $("message-count").textContent=data.visible_messages;
  $("period").textContent=data.earliest?`${date(data.earliest)} — ${date(data.latest)}`:"保存済み発言なし";
  const previous=$("room").value; $("room").replaceChildren(new Option("すべての閲覧可能ルーム",""));
  $("scopes").replaceChildren();
  for(const room of data.rooms){
    $("room").append(new Option(room.name,room.id));
    const row=node("div",undefined,"scope-row");row.append(node("span",room.name));
    const state=room.gap_suspected?"欠損の疑いあり":room.error_code?"同期エラー":room.last_sync?"同期確認："+date(room.last_sync,true):"履歴のみ（新着同期は未確認）";
    row.append(node("span",state,room.gap_suspected||room.error_code?"bad":""));$("scopes").append(row);
  }
  if(data.rooms.some(r=>r.id===previous))$("room").value=previous;
}
async function switchUser(subject){
  clearPrivateView();clearError();token="";currentSubject=subject;const generation=requestGeneration;
  try{
    const session=await api("/api/demo/session",{subject},false);
    if(generation!==requestGeneration)return;token=session.access_token;
    await refreshScope();await search();
  }catch(e){if(generation===requestGeneration)showError(e);}
}
async function search(){
  clearError();const generation=++requestGeneration;readerGeneration++;
  $("search-btn").disabled=true;$("result-state").textContent="検索中";
  $("results").replaceChildren();$("reader-content").replaceChildren(node("p","結果を選んで根拠を確認してください。","method-note"));
  try{
    const body={query:$("query").value,mode:$("search-mode").value,room_id:$("room").value||null};
    if($("start").value)body.start=Date.parse($("start").value+"T00:00:00+09:00")/1000;
    if($("end").value)body.end=Date.parse($("end").value+"T23:59:59+09:00")/1000;
    const data=await api("/api/search",body);if(generation!==requestGeneration)return;
    lastResults=data.results;$("result-count").textContent=data.results.length;
    $("result-state").textContent=data.meta.truncated?"一部の結果を表示":"許可済みの根拠";
    $("warnings").replaceChildren(...data.meta.warnings.map(t=>node("div",t,"warning")));
    if(!data.results.length)$("results").append(node("div","現在の閲覧範囲では見つかりませんでした。検索語や期間を変えてください。","empty-results"));
    for(const item of data.results){
      const card=node("button",undefined,"result-card");card.dataset.id=item.id;card.type="button";
      const meta=node("div",undefined,"card-meta");meta.append(node("span",item.room_name,"room-label"),node("span",date(item.sent_at)));
      const foot=node("div",undefined,"card-foot");foot.append(node("span",item.sender+" · "+(item.source==="snapshot"?"過去履歴":"最新ログ")),node("span","原文を読む →"));
      card.append(meta,node("h3",item.excerpt),foot);card.addEventListener("click",()=>read(item.id));$("results").append(card);
    }
    if(data.results.length)await read(data.results[0].id);
  }catch(e){if(generation===requestGeneration){showError(e);$("result-state").textContent="検索を停止しました";}}
  finally{if(generation===requestGeneration)$("search-btn").disabled=false;}
}
async function read(id){
  const generation=++readerGeneration;const userGeneration=requestGeneration;selectedMessage=id;
  document.querySelectorAll(".result-card").forEach(e=>e.classList.toggle("selected",e.dataset.id===id));
  $("reader-content").replaceChildren(node("p","原文の閲覧権限を確認中です。"));
  try{
    const d=await api("/api/messages/"+encodeURIComponent(id));
    if(generation!==readerGeneration||userGeneration!==requestGeneration)return;
    const root=$("reader-content");root.replaceChildren();
    const head=node("div",undefined,"reader-head");head.append(node("strong",d.room_name),node("span",d.sender));
    root.append(head,node("div",d.text,"reader-body"));
    const dl=node("dl");for(const [name,value] of [["発言日時",date(d.sent_at,true)+" JST"],["更新日時",date(d.updated_at,true)+" JST"],["確認した時点",date(d.observed_at,true)+" JST"],["取得元",d.source==="snapshot"?"履歴CSV（スナップショット）":"API / Webhook"],["メッセージID",d.remote_id||"元IDなし：内部参照IDで管理"]]){dl.append(node("dt",name),node("dd",value));}root.append(dl);
    for(const ref of d.metadata.references||[]){const b=node("button","返信・引用元を読む");b.addEventListener("click",()=>read(ref.id));root.append(b);}
    const link=node("a","この根拠のリンク","ref-link");link.href=d.url;link.target="_blank";link.rel="noopener noreferrer";const linkParagraph=node("p");linkParagraph.append(link);root.append(linkParagraph);
    const timeline=node("button","このルームの前後の会話を読む");timeline.addEventListener("click",()=>readTimeline(d));root.append(timeline);
    root.append(node("p","これは原文の表示です。生成AIによる要約ではありません。古い検討案を、現在の確定事項と混同しないでください。","annotation"));
  }catch(e){if(generation===readerGeneration&&userGeneration===requestGeneration){$("reader-content").replaceChildren(node("p",e.message,"error"));}}
}
async function readTimeline(item){
 const gen=++readerGeneration, ugen=requestGeneration;
 try{
  const start=Math.max(0,Date.parse(item.sent_at)/1000-86400*2),end=Date.parse(item.sent_at)/1000+86400*2;
  const result=await api(`/api/timeline?room_id=${encodeURIComponent(item.room_id)}&start=${start}&end=${end}&limit=100`);
  if(gen!==readerGeneration||ugen!==requestGeneration)return;
  const root=$("reader-content");root.replaceChildren(node("h3","前後2日間の保存済み会話"));
  for(const m of result.results){root.append(node("p",date(m.sent_at,true)+" · "+m.sender,"reader-head"),node("div",m.text,"reader-body"));}
  root.append(node("p",result.has_more?"100件を超えるため一部表示です。APIのnext_offsetで続きを取得できます。":result.notice,"annotation"));
 }catch(e){if(gen===readerGeneration)showError(e);}
}
async function action(name){
 clearError();try{const d=await api("/api/demo/"+name,{});
  const descriptions={"new-message":"新着発言を保存しました。「新着」で検索します。","import":"同じCSVを再確認しました。重複した発言は追加されません。","gap":"欠損警告を模擬表示しました。実際にデータは削除していません。","reset":"架空データと権限を初期状態へ戻しました。"};
  $("action-result").textContent=descriptions[name]||JSON.stringify(d);
  if(name==="new-message")$("query").value="新着";
  if(name==="reset")$("query").value="納期";
  await refreshScope();await search();
 }catch(e){showError(e);}
}
async function membership(enabled){
 const room={aoki:"101",sato:"202",mori:"303"}[currentSubject];
 try{clearPrivateView();await api("/api/demo/membership",{room_id:room,enabled});
  $("action-result").textContent=enabled?"担当ルームへ復帰しました。以後の検索で再び参照できます。":"担当ルームを退室しました。検索と原文取得の両方から除外されます。";
  await refreshScope();await search();
 }catch(e){showError(e);}
}
$("search-form").addEventListener("submit",e=>{e.preventDefault();search();});
$("person").addEventListener("change",()=>switchUser($("person").value));
document.querySelectorAll("[data-query]").forEach(b=>b.addEventListener("click",()=>{$("query").value=b.dataset.query;search();}));
document.querySelectorAll("[data-action]").forEach(b=>b.addEventListener("click",()=>action(b.dataset.action)));
$("leave-room").addEventListener("click",()=>membership(false));$("rejoin-room").addEventListener("click",()=>membership(true));
$("login").addEventListener("click",async()=>{clearPrivateView();token=$("gateway-token").value;$("gateway-token").value="";try{await refreshScope();await search();}catch(e){showError(e);}});
(async()=>{try{
 config=await api("/api/config",undefined,false);$("mode-badge").textContent=config.mode==="demo"?"架空データのデモ":"既存基盤との統合モード";
 if(config.embedding_enabled){const o=document.querySelector('option[value="hybrid"]');o.disabled=false;o.textContent="意味検索＋文字列";}
 if(config.mode==="demo"){
  config.people.forEach(p=>$("person").append(new Option(p.name,p.id)));await switchUser("aoki");
  const mid=new URLSearchParams(location.search).get("message");if(mid)await read(mid);
 }else{$("person").hidden=true;$("integration-login").hidden=false;$("demo-panel").hidden=true;$("notice").textContent="既存の認証基盤が発行したトークンで接続してください。デモ用の利用者切替は無効です。";$("identity-note").textContent="トークンはメモリ内だけで利用します。";}
}catch(e){showError(e);}})();
