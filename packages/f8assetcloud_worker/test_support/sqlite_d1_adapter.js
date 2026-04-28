import { DatabaseSync } from 'node:sqlite';

export function createSqliteD1Database({ migrationsSql }) {
  const database = new DatabaseSync(':memory:');
  database.exec(migrationsSql);
  return {
    prepare(sql) {
      const statement = database.prepare(sql);
      let bindings = [];
      return {
        bind(...values) {
          bindings = values.map((value) => normalizeBindingValue(value));
          return this;
        },
        async raw() {
          statement.setReturnArrays(true);
          try {
            return statement.all(...bindings);
          } finally {
            statement.setReturnArrays(false);
          }
        },
        async first() {
          const row = statement.get(...bindings);
          return row === undefined ? null : row;
        },
        async all() {
          const results = statement.all(...bindings);
          return { results };
        },
        async run() {
          const result = statement.run(...bindings);
          return {
            success: true,
            meta: {
              changes: Number(result.changes || 0),
              last_row_id: Number(result.lastInsertRowid || 0),
            },
          };
        },
      };
    },
    close() {
      database.close();
    },
  };
}

function normalizeBindingValue(value) {
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (typeof value === 'boolean') {
    return value ? 1 : 0;
  }
  return value;
}
