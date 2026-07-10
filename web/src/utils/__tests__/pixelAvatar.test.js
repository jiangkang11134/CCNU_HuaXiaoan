import assert from 'node:assert/strict'
import {
  AVATAR_BACKGROUND_TOKENS,
  generatePixelAvatar,
  getAvatarColorIndex,
  getAvatarFallbackStyle,
  getAvatarInitials
} from '../pixelAvatar.js'

const run = () => {
  {
    const first = generatePixelAvatar('user-001')
    const second = generatePixelAvatar('user-001')
    assert.equal(first, second, 'Same ID should generate the same avatar')
    console.log('T1 Stable output: PASS')
  }

  {
    const first = generatePixelAvatar('user-001')
    const second = generatePixelAvatar('user-002')
    assert.notEqual(first, second, 'Different IDs should generate different avatars')
    console.log('T2 Different IDs: PASS')
  }

  {
    const avatar = generatePixelAvatar('user-003')
    assert.ok(avatar.startsWith('data:image/svg+xml;charset=UTF-8,'), 'Avatar should be local SVG')
    console.log('T3 Local SVG avatar: PASS')
  }

  {
    const avatar = generatePixelAvatar(' user/中文 ')
    const decoded = decodeURIComponent(avatar.replace('data:image/svg+xml;charset=UTF-8,', ''))
    assert.match(decoded, />us<\//, 'Seed should be trimmed before rendering initials')
    console.log('T4 Trimmed seed: PASS')
  }

  {
    assert.throws(
      () => generatePixelAvatar(''),
      /requires an id/,
      'Empty ID should be treated as invalid data'
    )
    assert.throws(
      () => generatePixelAvatar(null),
      /requires an id/,
      'Null ID should be treated as invalid data'
    )
    console.log('T5 Missing ID fails: PASS')
  }

  {
    assert.equal(
      getAvatarInitials('张三丰', 'user'),
      '张三',
      'Chinese initials use first two chars'
    )
    assert.equal(getAvatarInitials('Alice', 'user'), 'Al', 'ASCII initials use first two chars')
    assert.equal(getAvatarInitials('', 'user'), '用户', 'User fallback should be localized')
    assert.equal(getAvatarInitials('', 'agent'), '智能', 'Agent fallback should be localized')
    console.log('T6 Initials: PASS')
  }

  {
    const first = getAvatarColorIndex('user-001')
    const second = getAvatarColorIndex('user-001')
    const third = getAvatarColorIndex('user-002')
    assert.equal(first, second, 'Same seed should select the same fallback color')
    assert.notEqual(first, third, 'Different seeds should be able to select different colors')
    assert.ok(first >= 0 && first < AVATAR_BACKGROUND_TOKENS.length, 'Color index should be valid')
    console.log('T7 Stable fallback color: PASS')
  }

  {
    const style = getAvatarFallbackStyle('agent-001')
    assert.equal(typeof style.background, 'string', 'Fallback style should include background')
    assert.equal(typeof style.color, 'string', 'Fallback style should include text color')
    console.log('T8 Fallback style: PASS')
  }

  console.log('\nAll 8 pixel avatar tests passed!')
}

run()
