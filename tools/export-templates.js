// Paste into the browser console on the TensorLearn site while signed in as a platform
// manager. It downloads code-lab-templates.json for tools/verify_templates.py.
// Pass lab IDs to export organization labs instead, e.g. exportLabs(['<lab id>', ...]).
async function exportTemplates() {
  const {templates} = await fetch('/api/learn/code-lab-templates').then((r) => r.json())
  const items = []
  for (const t of templates) {
    if (t.archived || t.latestVersion == null) continue
    const {version} = await fetch(`/api/learn/code-lab-templates/${t.id}/versions/${t.latestVersion}`).then((r) => r.json())
    items.push({name: `${t.title} v${t.latestVersion}`, definition: version.definition})
  }
  save(items, 'code-lab-templates.json')
}
async function exportLabs(ids) {
  const items = []
  for (const id of ids) {
    const lab = await fetch(`/api/workshops/${id}/code-lab`).then((r) => r.json())
    items.push({name: lab.title, definition: {exercises: lab.editor.exercises.filter((e) => !e.archived).map((e) => e.definition)}})
  }
  save(items, 'code-labs.json')
}
function save(items, name) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([JSON.stringify(items, null, 1)], {type: 'application/json'}))
  link.download = name
  link.click()
}
exportTemplates()
