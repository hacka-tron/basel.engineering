import assert from 'node:assert/strict'
import { test } from 'node:test'
import { storageKey } from './conversation.ts'
import { apiCorpus, CORPORA, idkCorpus, topicLabel, TOPICS } from './topics.ts'

test('three topics in display order, with full and short labels', () => {
  assert.deepEqual(TOPICS.map((topic) => [topic.value, topic.label, topic.short]), [
    ['basel', 'About Basel', 'Basel'],
    ['system', 'About This System', 'System'],
    ['portfolio', 'Portfolio', 'Portfolio'],
  ])
  assert.deepEqual([...CORPORA], ['basel', 'system', 'portfolio'])
})

test('each topic maps to its API corpus and its own saved conversation', () => {
  assert.deepEqual(CORPORA.map(apiCorpus), ['about_me', 'about_system', 'portfolio'])
  assert.equal(new Set(CORPORA.map((corpus) => storageKey(apiCorpus(corpus)))).size, 3)
  assert.equal(storageKey(apiCorpus('portfolio')), 'glassbox:conv:v1:portfolio')
})

test('labels and the "I don\'t know" pool per topic', () => {
  assert.equal(topicLabel('portfolio'), 'Portfolio')
  assert.equal(topicLabel('basel'), 'About Basel')
  assert.equal(idkCorpus('system'), 'system')
  assert.equal(idkCorpus('portfolio'), 'basel')
  assert.equal(idkCorpus('basel'), 'basel')
})
