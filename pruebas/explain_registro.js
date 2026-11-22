// explain("executionStats") de las consultas del registro (A3.2 y A3.3).
// Uso (desde la raíz del repo):
//   docker compose exec -T mongo1 mongosh --quiet \
//     "mongodb://mongo1,mongo2,mongo3/plataforma?replicaSet=rs0" --eval "$(cat pruebas/explain_registro.js)"
// findAndModify en modo explain no modifica datos.

// Proyecto LiDAR de ejemplo e instante actual.
const P = db.proyectos.findOne({ nombre: "LiDAR urbano 2026" })._id;
const ahora = new Date();
// Mismo filtro que POST /tareas/asignar: libre, o abierta con el plazo vencido.
const libre = { $or: [{ estado: "disponible" }, { estado: "abierta", "asignacion.vence_en": { $lt: ahora } }] };
// Cambio que aplica la asignación (solo para el explain; no se ejecuta).
const cambio = { $set: { estado: "abierta" }, $inc: { version: 1 } };

// Convierte el árbol del plan en texto: ETAPA(índice) <- etapa hija <- ...
function etapas(n) {
  if (!n) return "";
  const yo = n.stage + (n.indexName ? "(" + n.indexName + ")" : "");
  const hijos = n.inputStage ? [n.inputStage] : (n.inputStages || []);
  return hijos.length ? yo + " <- " + hijos.map(etapas).join(" + ") : yo;
}

// Imprime el plan ganador y las estadísticas de ejecución (claves y documentos examinados).
function resumen(nombre, exp) {
  const e = exp.executionStats;
  const plan = exp.queryPlanner.winningPlan.queryPlan || exp.queryPlanner.winningPlan;
  print("== " + nombre);
  print("   plan: " + etapas(plan));
  print("   nReturned=" + e.nReturned + "  totalKeysExamined=" + e.totalKeysExamined +
        "  totalDocsExamined=" + e.totalDocsExamined + "  executionTimeMillis=" + e.executionTimeMillis);
}

// Contexto: cuántas tareas hay y qué índices existen.
print("tareas: " + db.tareas.countDocuments({}) + " (" +
      db.tareas.countDocuments({ estado: "aprobada", proyecto_id: P }) + " aprobadas en el proyecto LiDAR)");
print("índices de tareas: " + db.tareas.getIndexes().map(i => i.name).join(", "));
print("índices de proyectos: " + db.proyectos.getIndexes().map(i => i.name).join(", "));
// Las tres consultas que se analizan.
resumen("asignación con proyecto_id",
  db.tareas.explain("executionStats").findAndModify({ query: Object.assign({ proyecto_id: P }, libre), sort: { prioridad: -1 }, update: cambio }));
resumen("asignación sin proyecto_id (cola global)",
  db.tareas.explain("executionStats").findAndModify({ query: libre, sort: { prioridad: -1 }, update: cambio }));
resumen("exportación, primera página (limite 100)",
  db.tareas.find({ proyecto_id: P, estado: "aprobada" }).sort({ _id: 1 }).limit(100).explain("executionStats"));
