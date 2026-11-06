// UI navigation for the existing public room. No invented rooms or online counts.
const $ = (id) => document.getElementById(id);
const authors = new Set();
const previews = new Map();
let members = [], hooks;
const clock = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit' });

export function openRoom() { document.body.dataset.mobileRoom = 'true'; }

function view(name) {
  document.body.dataset.view = name;
  document.body.dataset.mobileRoom = 'false';
  for (const button of document.querySelectorAll('.rail-button[data-view]')) {
    const active = button.dataset.view === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
  $('list-title').textContent = { friends: '참여자', chats: '채팅', more: '더보기' }[name];
  for (const key of ['friends', 'chats', 'more']) $(`${key}-view`).hidden = key !== name;
  $('list-search-wrap').hidden = name === 'more';
  $('list-search').value = '';
  renderMembers(); filterList();
}

function profile() {
  const name = $('name').value.trim();
  $('profile-name').textContent = name || '참여자';
  $('my-initial').textContent = [...name][0] || '나';
}

function renderMembers() {
  const names = [...new Set([...members, ...authors])].filter(Boolean);
  $('friend-count').textContent = String(names.length);
  $('room-member-count').textContent = names.length ? String(names.length) : '';
  $('list-members').textContent = names.length ? String(names.length) : '공개방';
  const query = $('list-search').value.trim().toLocaleLowerCase();
  const items = names.filter(name => name.toLocaleLowerCase().includes(query)).slice(0, 100).map(name => {
    const li = document.createElement('li'), button = document.createElement('button');
    button.type = 'button';
    const avatar = document.createElement('span'); avatar.className = 'friend-avatar'; avatar.textContent = [...name][0]; avatar.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span'), title = document.createElement('strong'), note = document.createElement('small');
    title.textContent = name; note.textContent = '브라보 라운지 참여자'; text.append(title, note);
    button.append(avatar, text); button.onclick = openRoom; li.append(button); return li;
  });
  if (!items.length) {
    const li = document.createElement('li'); li.className = 'sidebar-empty';
    li.textContent = query ? '검색 결과가 없어요.' : '첫 메시지나 포인트 지갑을 만들면 여기에 표시돼요.';
    items.push(li);
  }
  $('friends-list').replaceChildren(...items); profile();
}

function filterList() {
  const query = $('list-search').value.trim().toLocaleLowerCase();
  const match = ['브라보 라운지', ...previews.values()].some(value => value.toLocaleLowerCase().includes(query));
  $('room-open').hidden = !match; $('list-empty').hidden = match;
}

export function filterMessages() {
  const query = $('room-search').value.trim().toLocaleLowerCase();
  let found = 0;
  for (const el of $('log').querySelectorAll('.msg')) {
    const match = !query || el.textContent.toLocaleLowerCase().includes(query);
    el.hidden = !match;
    if (match) found++;
  }
  $('search-count').textContent = query ? `${found}건` : '';
}

export function received(row, summary) {
  if (authors.size < 100) authors.add(row.author);
  previews.set(row.id, summary);
  if (previews.size > 500) previews.delete(Math.min(...previews.keys()));
  if (row.id === Math.max(...previews.keys())) {
    $('room-preview').textContent = summary;
    $('room-time').textContent = clock.format(new Date(row.created_at));
  }
  filterList(); renderMembers();
}

export function updateMembers(names) { members = names; renderMembers(); }

export function init(options) {
  hooks = options;
  $('chat-date').textContent = new Intl.DateTimeFormat('ko-KR', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  for (const button of document.querySelectorAll('.rail-button[data-view]')) button.onclick = () => view(button.dataset.view);
  $('room-open').onclick = openRoom;
  $('room-back').onclick = () => view('chats');
  $('nav-wallet').onclick = $('more-wallet').onclick = () => hooks.showTab('home');
  $('composer-pay').onclick = () => hooks.showTab('send');
  $('wallet-close').onclick = () => $('wallet-dialog').close();
  $('my-profile').onclick = () => { openRoom(); $('name').focus(); $('name').select(); };
  $('name').addEventListener('input', () => {
    profile();
    try { sessionStorage.setItem('bravo-alias', $('name').value); } catch { /* Private browsing. */ }
  });
  $('invite').onclick = () => $('share').click();
  $('more-observe').onclick = () => { openRoom(); if (!document.body.classList.contains('panel-open')) $('panel-toggle').click(); };
  $('room-search-toggle').onclick = () => {
    const open = $('room-search-panel').hidden;
    $('room-search-panel').hidden = !open;
    $('room-search-toggle').setAttribute('aria-expanded', String(open));
    if (open) $('room-search').focus();
    else { $('room-search').value = ''; filterMessages(); }
  };
  $('room-search').oninput = filterMessages;
  $('list-search').oninput = () => { filterList(); renderMembers(); };
  const emojis = ['😀','😊','😂','🥰','😎','🥳','👍','👏','🙏','💛','❤️','✨','🎉','☕','🍀','👋','🙌','💪'];
  $('emoji-picker').replaceChildren(...emojis.map(emoji => {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = emoji;
    button.setAttribute('aria-label', `${emoji} 입력`);
    button.onclick = () => {
      const input = $('body'), start = input.selectionStart, end = input.selectionEnd;
      if (input.value.length - (end - start) + emoji.length > input.maxLength) return;
      input.setRangeText(emoji, start, end, 'end'); input.dispatchEvent(new Event('input'));
      $('emoji-picker').hidden = true; $('emoji-toggle').setAttribute('aria-expanded', 'false'); input.focus();
    };
    return button;
  }));
  $('emoji-toggle').onclick = () => {
    $('emoji-picker').hidden = !$('emoji-picker').hidden;
    $('emoji-toggle').setAttribute('aria-expanded', String(!$('emoji-picker').hidden));
  };
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    $('emoji-picker').hidden = true; $('emoji-toggle').setAttribute('aria-expanded', 'false');
    if (document.body.classList.contains('panel-open')) $('panel-close').click();
  });
  profile();
}
