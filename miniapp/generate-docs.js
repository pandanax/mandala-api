const fs = require('fs');
const path = require('path');

const IGNORE_DIRS = ['dist', 'node_modules']; // Добавлено node_modules в игнорируемые директории
const IGNORE_FILENAMES = ['package-lock.json', '.DS_Store', 'LICENSE'];
const IGNORE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.md', '.css'];

const TARGET_DIR = process.env.DIR || './app';
const OUTPUT_FILE = `doc-${TARGET_DIR.replace(/[^a-zA-Z0-9]/g, '-')}.md`;

function isSpecialDirectory(name) {
    return name === '.github'; // Убрали node_modules, так как она теперь в IGNORE_DIRS
}

function shouldIgnore(itemPath) {
    const basename = path.basename(itemPath);
    const dirname = path.dirname(itemPath);
    const ext = path.extname(basename).toLowerCase();

    if (isSpecialDirectory(basename)) {
        return path.relative(TARGET_DIR, dirname) !== '';
    }

    const isIgnoredDir = IGNORE_DIRS.some(dir => {
        // Проверяем, содержит ли путь игнорируемую директорию как отдельный компонент
        const pathParts = itemPath.split(path.sep);
        return pathParts.includes(dir);
    });

    const isIgnoredFile = IGNORE_FILENAMES.includes(basename);
    const isHidden = basename.startsWith('.') && !isSpecialDirectory(basename);
    const isImage = IGNORE_EXTENSIONS.includes(ext);

    return isIgnoredDir || isIgnoredFile || isHidden || isImage;
}

function getFileContents() {
    let contents = '';

    function traverse(dir) {
        const items = fs.readdirSync(dir);

        items.forEach(item => {
            const fullPath = path.join(dir, item);
            if (shouldIgnore(fullPath)) return;

            const stats = fs.statSync(fullPath);
            const ext = path.extname(item).toLowerCase();

            if (stats.isDirectory()) {
                traverse(fullPath);
            } else {
                try {
                    if (IGNORE_EXTENSIONS.includes(ext)) {
                        const projectRelativePath = path.relative(process.cwd(), fullPath);
                        contents += `\n\n## ${projectRelativePath}\n<Файл изображения пропущен>\n`;
                        return;
                    }

                    const data = fs.readFileSync(fullPath, 'utf8');
                    const projectRelativePath = path.relative(process.cwd(), fullPath);
                    contents += `\n\n## ${projectRelativePath}\n\`\`\`\n${data}\n\`\`\`\n`;
                } catch (e) {
                    const projectRelativePath = path.relative(process.cwd(), fullPath);
                    contents += `\n\n## ${projectRelativePath}\n<Не удалось прочитать файл>\n`;
                }
            }
        });
    }

    traverse(TARGET_DIR);
    return contents;
}

function getFileTree(rootDir, prefix = '', depth = 0) {
    let result = '';
    const items = fs.readdirSync(rootDir).filter(item => {
        const fullPath = path.join(rootDir, item);
        return !shouldIgnore(fullPath);
    });

    items.forEach((item, index) => {
        const fullPath = path.join(rootDir, item);
        const isLast = index === items.length - 1;
        const stats = fs.statSync(fullPath);
        const relativePath = path.relative(TARGET_DIR, fullPath);

        result += prefix + (isLast ? '└── ' : '├── ') + relativePath + '\n';

        if (stats.isDirectory()) {
            if (item === 'node_modules') return;

            const newPrefix = prefix + (isLast ? '    ' : '│   ');
            result += getFileTree(fullPath, newPrefix, depth + 1);
        }
    });

    return result;
}

function generateDocumentation() {
    if (!fs.existsSync(TARGET_DIR)) {
        console.error(`Директория ${TARGET_DIR} не существует!`);
        return;
    }

    if (fs.existsSync(OUTPUT_FILE)) {
        try {
            fs.unlinkSync(OUTPUT_FILE);
            console.log(`Существующий файл ${OUTPUT_FILE} удален`);
        } catch (err) {
            console.error(`Ошибка при удалении файла: ${err.message}`);
            return;
        }
    }

    const header = `# Исходный код проекта (${TARGET_DIR})\n\n` +
        `Это полный исходный код проекта, внимательно изучите структуру проекта и содержимое файлов.\n\n` +
        `## Структура проекта\n\`\`\`\n${getFileTree(TARGET_DIR)}\`\`\`\n` +
        `## Содержимое файлов\n`;

    const contents = getFileContents();

    try {
        fs.writeFileSync(
            OUTPUT_FILE,
            header + contents,
            'utf8'
        );
        console.log(`Документация создана: ${OUTPUT_FILE}`);
    } catch (err) {
        console.error(`Ошибка при создании файла: ${err.message}`);
    }
}

generateDocumentation();
