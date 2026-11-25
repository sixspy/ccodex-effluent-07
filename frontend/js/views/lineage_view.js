export const lineageViewTemplate = `
  <div class="grid-2">
    <div class="panel">
      <h3>指标血缘（仅正式链，影子边不展）</h3>
      <table>
        <thead><tr><th>编号</th><th>从</th><th>到</th><th>边类型</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="e in lineageEdges" :key="e.id">
            <td>#{{ e.id }}</td><td>{{ e.from_code }}</td><td>{{ e.to_code || "—" }}</td>
            <td>{{ e.edge_type }}</td><td>{{ e.status }}</td><td>{{ e.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>登记血缘边</h3>
      <div class="form-row"><div><label>源采样 ID</label><input type="number" v-model.number="lineageForm.from_sample_id"/></div>
      <div><label>目标 ID</label><input type="number" v-model.number="lineageForm.to_sample_id"/></div></div>
      <label>边类型</label>
      <select v-model="lineageForm.edge_type">
        <option value="parent_child">parent_child（父子）</option>
        <option value="复测续批">复测续批</option>
        <option value="准排锚点">准排锚点</option>
      </select>
      <label>说明</label><input v-model="lineageForm.detail"/>
      <div class="actions"><button @click="createLineage">写入</button></div>
    </div>
  </div>`;
