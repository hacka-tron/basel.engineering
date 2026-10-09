import assert from 'node:assert/strict'
import { test } from 'node:test'
import { storageKey } from './conversation.ts'
import { apiCorpus, CORPORA, idkCorpus, panelView, topicForView, topicLabel, TOPICS } from './topics.ts'

test('two topics in display order, with full and short labels', () => {
  assert.deepEqual(TOPICS.map((topic) => [topic.value, topic.label, topic.short]), [
    ['basel', 'About Basel', 'Basel'],
    ['system', 'About This System', 'System'],
  ])
  assert.deepEqual([...CORPORA], ['basel', 'system'])
})

test('each topic maps to its API corpus and its own saved conversation', () => {
  assert.deepEqual(CORPORA.map(apiCorpus), ['about_me', 'about_system'])
  assert.equal(new Set(CORPORA.map((corpus) => storageKey(apiCorpus(corpus)))).size, 2)
})

test('labels and the "I don\'t know" pool per topic', () => {
  assert.equal(topicLabel('basel'), 'About Basel')
  assert.equal(topicLabel('system'), 'About This System')
  assert.equal(idkCorpus('system'), 'system')
  assert.equal(idkCorpus('basel'), 'basel')
})

test('each topic owns its panel, and each panel its topic (phone option A)', () => {
  assert.equal(panelView('basel', true), 'portfolio')
  assert.equal(panelView('system', true), 'diagram')
  for (const corpus of CORPORA) assert.equal(topicForView(panelView(corpus, true)), corpus)
  // No published project: About Basel falls back to the diagram.
  assert.equal(panelView('basel', false), 'diagram')
})
