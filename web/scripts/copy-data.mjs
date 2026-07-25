import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { access, cp, mkdir, readFile, writeFile } from 'node:fs/promises'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const webRoot = path.resolve(__dirname, '..')
const repoRoot = path.resolve(webRoot, '..')
const publicDir = path.join(webRoot, 'public')

function _metadataFromConfig(config, fallbackMetadata = {}) {
  const metadata = { ...fallbackMetadata }

  for (const model of config.models || []) {
    if (!model?.name) continue
    metadata[model.name] = {
      ...metadata[model.name],
      supportsVision: model.supports_vision !== false
    }
  }

  return metadata
}

async function _writeModelMetadata() {
  const configPath = path.join(repoRoot, 'problems', 'config.json')
  const fallbackPath = path.join(webRoot, 'model_metadata.json')
  const outputPath = path.join(publicDir, 'model_metadata.json')
  let metadata
  let sourcePath = configPath
  let fallbackMetadata = {}

  try {
    fallbackMetadata = JSON.parse(await readFile(fallbackPath, 'utf8'))
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw error
    }
  }

  try {
    const config = JSON.parse(await readFile(configPath, 'utf8'))
    metadata = _metadataFromConfig(config, fallbackMetadata)
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw error
    }
    metadata = fallbackMetadata
    sourcePath = fallbackPath
    console.warn(`Using fallback model metadata: ${path.relative(repoRoot, fallbackPath)}`)
  }

  await writeFile(outputPath, `${JSON.stringify(metadata, null, 2)}\n`, 'utf8')
  console.log(`Generated ${path.relative(repoRoot, outputPath)} from ${path.relative(repoRoot, sourcePath)}`)
}

async function _copyIfExists(sourcePath, targetPath, options = {}) {
  try {
    await mkdir(path.dirname(targetPath), { recursive: true })
    await cp(sourcePath, targetPath, { force: true })
    if (!options.silent) {
      console.log(`Copied ${path.relative(repoRoot, sourcePath)} -> ${path.relative(repoRoot, targetPath)}`)
    }
  } catch (error) {
    if (error.code === 'ENOENT' && options.optional) {
      console.warn(`Skipping missing file: ${path.relative(repoRoot, sourcePath)}`)
      return
    }
    throw error
  }
}

const PUBLIC_DATA_FILE_RULES = {
  results: /(?:^|_)results\.json$/,
  tokenUsage: /(?:^|_)token_usage\.json$/,
  questionsMetadata: /^questions(?:_metadata)?\.json$/
}

export function safeBenchmarkDataPath(filePath, field) {
  if (typeof filePath !== 'string'
    || path.isAbsolute(filePath)
    || path.win32.isAbsolute(filePath)) {
    throw new Error(`Invalid benchmark data path: ${filePath}`)
  }

  const normalized = path.posix.normalize(filePath.replaceAll('\\', '/'))
  const segments = normalized.split('/')
  const fileName = segments.at(-1) || ''
  const rule = PUBLIC_DATA_FILE_RULES[field]
  const containsPrivateProblemPath = segments.some(
    (segment) => segment.toLowerCase() === 'problems'
  )
  const isMetadataPath = field === 'questionsMetadata'
    && (segments.length === 1
      || (segments.length === 3 && segments[0] === 'benchmarks'))
  const isModeDataPath = field !== 'questionsMetadata' && segments.length === 1

  if (!rule
    || !normalized
    || normalized === '.'
    || normalized === '..'
    || normalized.startsWith('../')
    || segments.some((segment) => !segment || segment.startsWith('.'))
    || containsPrivateProblemPath
    || !rule.test(fileName)
    || (!isMetadataPath && !isModeDataPath)) {
    throw new Error(`Invalid benchmark data path: ${filePath}`)
  }

  return segments.join(path.sep)
}

async function _copyBenchmarkData(options = {}) {
  const registryPath = path.join(repoRoot, 'benchmarks', 'registry.json')
  const publicRegistryPath = path.join(publicDir, 'benchmark_registry.json')
  const registry = JSON.parse(await readFile(registryPath, 'utf8'))
  const defaultBenchmark = registry.defaultBenchmark || registry.benchmarks?.[0]?.id
  const copied = new Set()

  await _copyIfExists(registryPath, publicRegistryPath)

  for (const benchmark of registry.benchmarks || []) {
    const benchmarkMetadata = benchmark.questionsMetadata
    if (benchmarkMetadata) {
      const relativePath = safeBenchmarkDataPath(benchmarkMetadata, 'questionsMetadata')
      if (!copied.has(relativePath)) {
        await _copyIfExists(
          path.join(repoRoot, relativePath),
          path.join(publicDir, relativePath),
          { optional: true }
        )
        copied.add(relativePath)
      }
    }

    for (const [modeName, mode] of Object.entries(benchmark.modes || {})) {
      if (options.skipHard && modeName === 'hard') continue

      for (const [field, filePath] of Object.entries({
        results: mode.results,
        tokenUsage: mode.tokenUsage,
        questionsMetadata: mode.questionsMetadata
      })) {
        if (!filePath) continue
        const relativePath = safeBenchmarkDataPath(filePath, field)
        if (copied.has(relativePath)) continue

        const required = benchmark.id === defaultBenchmark
          && modeName === 'default'
          && field === 'results'
        let sourcePath = path.join(repoRoot, relativePath)
        if (field === 'tokenUsage') {
          try {
            await access(sourcePath)
          } catch (error) {
            if (error.code !== 'ENOENT') throw error
            sourcePath = path.join(repoRoot, 'problems', relativePath)
          }
        }
        await _copyIfExists(
          sourcePath,
          path.join(publicDir, relativePath),
          { optional: !required }
        )
        copied.add(relativePath)
      }
    }
  }
}

export async function copyDataFiles(options = {}) {
  await mkdir(publicDir, { recursive: true })

  await _copyBenchmarkData(options)

  await _writeModelMetadata()
}

if (process.argv[1] === __filename) {
  copyDataFiles({
    skipHard: process.argv.includes('--skip-hard')
  }).catch((error) => {
    console.error(error)
    process.exit(1)
  })
}
