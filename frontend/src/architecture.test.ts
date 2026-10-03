import assert from 'node:assert/strict'
import { test } from 'node:test'
import { architectureEdges, architectureNodes, PORTRAIT_ROW_PITCH, portraitEdges, portraitNodes, questionForComponent } from './architecture.ts'

const NODE_WIDTH = 124

test('the portrait layout places every component exactly once, in two columns', () => {
  assert.equal(portraitNodes.length, architectureNodes.length)
  const cells = new Set(portraitNodes.map((node) => `${node.position.x},${node.position.y}`))
  assert.equal(cells.size, portraitNodes.length)
  const columns = new Set(portraitNodes.map((node) => node.position.x))
  assert.equal(columns.size, 2)
})

test('the portrait graph fits a phone at the minimum readable zoom', () => {
  const width = Math.max(...portraitNodes.map((node) => node.position.x)) + NODE_WIDTH
  const height = Math.max(...portraitNodes.map((node) => node.position.y)) + 42
  // 0.75 is the mobile fitView floor; the diagram keeps at least 280px tall.
  assert.ok(width * 0.75 <= 343, `width ${width}`)
  assert.ok(height * 0.75 <= 280, `height ${height}`)
})

test('portrait edges keep the same connections and only flow right or down', () => {
  assert.deepEqual(portraitEdges.map((edge) => edge.id), architectureEdges.map((edge) => edge.id))
  const position = Object.fromEntries(portraitNodes.map((node) => [node.id, node.position]))
  for (const edge of portraitEdges) {
    const source = position[edge.source]
    const target = position[edge.target]
    if (edge.sourceHandle === 'right') {
      assert.equal(edge.targetHandle, 'left', edge.id)
      assert.equal(source.y, target.y, edge.id)
      assert.ok(target.x > source.x, edge.id)
    } else {
      assert.equal(edge.sourceHandle, 'bottom', edge.id)
      assert.equal(edge.targetHandle, 'top', edge.id)
      assert.ok(target.y >= source.y + PORTRAIT_ROW_PITCH, edge.id)
    }
  }
})

test('component questions do not ask the model to cite sources (the UI lists them)', () => {
  for (const node of architectureNodes) {
    const question = questionForComponent(node.id)
    assert.ok(question.endsWith('Explain its role in a request.'))
    assert.ok(!/cite/i.test(question))
  }
})
