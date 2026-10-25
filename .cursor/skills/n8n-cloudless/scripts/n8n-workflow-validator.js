/**
 * n8n Workflow Validator — vendored from github.com/kingler/n8n_agent
 * (n8n-workflow-validator.js). Kept verbatim; use ./validate-n8n-workflow.js
 * as the CLI entrypoint.
 */
/**
 * n8n Workflow Validator
 * 
 * This module provides validation functionality for n8n workflows to ensure
 * they follow best practices for naming, error handling, security, performance,
 * and documentation.
 */

/**
 * Validates naming conventions in a workflow
 * @param {Object} workflow - The workflow object to validate
 * @param {string} strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results with issues and suggestions
 */
function validateNaming(workflow, strictness = 'medium') {
  const issues = [];
  const suggestions = [];
  
  // Check workflow name
  if (!workflow.name) {
    issues.push('Workflow name is missing');
    suggestions.push('Add a descriptive name to the workflow');
  } else if (workflow.name.length < 5 && strictness !== 'low') {
    issues.push('Workflow name is too short');
    suggestions.push('Use a more descriptive name that indicates the workflow\'s purpose');
  }
  
  // Check node names
  if (workflow.nodes && Array.isArray(workflow.nodes)) {
    const defaultNodeNames = new Set();
    const duplicateNodeNames = new Set();
    
    workflow.nodes.forEach(node => {
      // Check for default names (ones that include the node type)
      if (node.name && node.type) {
        const nodeTypeName = node.type.split('.').pop();
        if (node.name.includes(nodeTypeName)) {
          defaultNodeNames.add(node.name);
        }
      }
      
      // Check for duplicate names
      if (node.name) {
        const count = workflow.nodes.filter(n => n.name === node.name).length;
        if (count > 1) {
          duplicateNodeNames.add(node.name);
        }
      }
    });
    
    if (defaultNodeNames.size > 0 && strictness !== 'low') {
      issues.push(`${defaultNodeNames.size} nodes have default names`);
      suggestions.push('Rename nodes to better describe their purpose in the workflow');
    }
    
    if (duplicateNodeNames.size > 0) {
      issues.push(`Found ${duplicateNodeNames.size} duplicate node names`);
      suggestions.push('Ensure each node has a unique name to avoid confusion');
    }
  }
  
  return {
    category: 'naming',
    passed: issues.length === 0,
    issues,
    suggestions
  };
}

/**
 * Validates error handling in a workflow
 * @param {Object} workflow - The workflow object to validate
 * @param {string} strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results with issues and suggestions
 */
function validateErrorHandling(workflow, strictness = 'medium') {
  const issues = [];
  const suggestions = [];
  
  // Check if workflow has error handling
  const hasErrorTrigger = workflow.nodes && 
    Array.isArray(workflow.nodes) && 
    workflow.nodes.some(node => node.type === 'n8n-nodes-base.errorTrigger');
  
  const hasErrorWorkflow = workflow.settings && workflow.settings.errorWorkflow;
  
  if (!hasErrorTrigger && !hasErrorWorkflow && strictness !== 'low') {
    issues.push('No error handling found in workflow');
    suggestions.push('Add an Error Trigger node or set an error workflow in the settings');
  }
  
  // Check for HTTP nodes without error handling
  if (workflow.nodes && Array.isArray(workflow.nodes)) {
    const httpNodes = workflow.nodes.filter(node => 
      node.type === 'n8n-nodes-base.httpRequest' || 
      node.type.toLowerCase().includes('http')
    );
    
    if (httpNodes.length > 0) {
      const hasHttpErrorHandling = httpNodes.some(node => {
        // Check if this HTTP node has connections to an IF node or Error node
        if (workflow.connections && workflow.connections[node.name]) {
          const connections = workflow.connections[node.name].main;
          if (!connections || !Array.isArray(connections)) return false;
          
          // Check all connections from this HTTP node
          return connections.some(connArray => {
            if (!Array.isArray(connArray)) return false;
            
            return connArray.some(conn => {
              const targetNode = workflow.nodes.find(n => n.name === conn.node);
              return targetNode && (
                targetNode.type === 'n8n-nodes-base.if' || 
                targetNode.type.includes('error') ||
                targetNode.type === 'n8n-nodes-base.function'
              );
            });
          });
        }
        return false;
      });
      
      if (!hasHttpErrorHandling && strictness !== 'low') {
        issues.push('HTTP request nodes without error handling');
        suggestions.push('Add error handling for HTTP requests using IF nodes or Error nodes');
      }
    }
  }
  
  return {
    category: 'errorHandling',
    passed: issues.length === 0,
    issues,
    suggestions
  };
}

/**
 * Validates security practices in a workflow
 * @param {Object} workflow - The workflow object to validate
 * @param {string} strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results with issues and suggestions
 */
function validateSecurity(workflow, strictness = 'medium') {
  const issues = [];
  const suggestions = [];
  
  // Check for hard-coded credentials
  if (workflow.nodes && Array.isArray(workflow.nodes)) {
    // Look for nodes with parameters that might contain sensitive information
    const nodesWithPotentialHardcodedCredentials = workflow.nodes.filter(node => {
      if (node.parameters) {
        const paramValues = JSON.stringify(node.parameters).toLowerCase();
        return (
          paramValues.includes('api') && paramValues.includes('key') ||
          paramValues.includes('token') ||
          paramValues.includes('secret') ||
          paramValues.includes('password') ||
          paramValues.includes('auth')
        );
      }
      return false;
    });
    
    if (nodesWithPotentialHardcodedCredentials.length > 0 && strictness !== 'low') {
      issues.push(`${nodesWithPotentialHardcodedCredentials.length} nodes potentially contain hard-coded credentials`);
      suggestions.push('Use credential objects instead of hard-coding sensitive information');
    }
  }
  
  // Check for proper credential usage
  const credentialTypes = new Set();
  if (workflow.nodes && Array.isArray(workflow.nodes)) {
    workflow.nodes.forEach(node => {
      if (node.credentials) {
        Object.keys(node.credentials).forEach(key => {
          credentialTypes.add(key);
        });
      }
    });
  }
  
  if (credentialTypes.size === 0 && strictness === 'high') {
    issues.push('No credential objects used in workflow');
    suggestions.push('Consider using credential objects for better security');
  }
  
  // Check for webhook nodes with proper authentication
  const webhookNodes = workflow.nodes && Array.isArray(workflow.nodes) 
    ? workflow.nodes.filter(node => node.type === 'n8n-nodes-base.webhook')
    : [];
    
  if (webhookNodes.length > 0) {
    const unsecuredWebhooks = webhookNodes.filter(node => {
      if (!node.parameters) return true;
      
      // Check if authentication is enabled
      const hasAuth = node.parameters.authentication === true ||
                     (node.parameters.options && node.parameters.options.authentication);
      
      return !hasAuth;
    });
    
    if (unsecuredWebhooks.length > 0 && strictness !== 'low') {
      issues.push(`${unsecuredWebhooks.length} webhook nodes without authentication`);
      suggestions.push('Enable authentication on webhook nodes to prevent unauthorized access');
    }
  }
  
  return {
    category: 'security',
    passed: issues.length === 0,
    issues,
    suggestions
  };
}

/**
 * Validates performance aspects of a workflow
 * @param {Object} workflow - The workflow object to validate
 * @param {string} strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results with issues and suggestions
 */
function validatePerformance(workflow, strictness = 'medium') {
  const issues = [];
  const suggestions = [];
  
  // Check for workflow complexity
  if (workflow.nodes && Array.isArray(workflow.nodes)) {
    if (workflow.nodes.length > 50 && strictness !== 'low') {
      issues.push(`Workflow has ${workflow.nodes.length} nodes, which may impact performance`);
      suggestions.push('Consider breaking down complex workflows into smaller sub-workflows');
    }
    
    // Check for potential loops
    const hasLoopNodes = workflow.nodes.some(node => 
      node.type === 'n8n-nodes-base.splitInBatches' || 
      node.type === 'n8n-nodes-base.loop'
    );
    
    if (hasLoopNodes) {
      const loopNodes = workflow.nodes.filter(node => 
        node.type === 'n8n-nodes-base.splitInBatches' || 
        node.type === 'n8n-nodes-base.loop'
      );
      
      // Check if loop batch size is reasonable
      loopNodes.forEach(node => {
        if (node.parameters && node.parameters.batchSize && node.parameters.batchSize > 100) {
          issues.push(`Loop node "${node.name}" has a large batch size (${node.parameters.batchSize})`);
          suggestions.push('Reduce batch size in loop nodes to prevent performance issues');
        }
      });
    }
    
    // Check for HTTP polling patterns
    const hasMultipleHttpRequests = workflow.nodes.filter(node => 
      node.type === 'n8n-nodes-base.httpRequest'
    ).length > 5;
    
    if (hasMultipleHttpRequests && strictness !== 'low') {
      issues.push('Workflow contains multiple HTTP request nodes');
      suggestions.push('Consider using webhook triggers where possible instead of multiple HTTP requests');
    }
  }
  
  return {
    category: 'performance',
    passed: issues.length === 0,
    issues,
    suggestions
  };
}

/**
 * Validates documentation in a workflow
 * @param {Object} workflow - The workflow object to validate
 * @param {string} strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results with issues and suggestions
 */
function validateDocumentation(workflow, strictness = 'medium') {
  const issues = [];
  const suggestions = [];
  
  // Check workflow description
  if (!workflow.description && strictness !== 'low') {
    issues.push('Workflow description is missing');
    suggestions.push('Add a detailed description explaining the workflow\'s purpose and functionality');
  } else if (workflow.description && workflow.description.length < 30 && strictness === 'high') {
    issues.push('Workflow description is too brief');
    suggestions.push('Provide a more detailed workflow description');
  }
  
  // Check for sticky notes
  const stickyNotes = workflow.nodes && Array.isArray(workflow.nodes) 
    ? workflow.nodes.filter(node => node.type === 'n8n-nodes-base.stickyNote')
    : [];
  
  if (stickyNotes.length === 0 && workflow.nodes && workflow.nodes.length > 10 && strictness !== 'low') {
    issues.push('No sticky notes found in a complex workflow');
    suggestions.push('Add sticky notes to document workflow sections and complex logic');
  }
  
  // Check for tags
  if (!workflow.tags || !Array.isArray(workflow.tags) || workflow.tags.length === 0) {
    issues.push('No tags defined for the workflow');
    suggestions.push('Add relevant tags to make the workflow more discoverable');
  }
  
  return {
    category: 'documentation',
    passed: issues.length === 0,
    issues,
    suggestions
  };
}

/**
 * Validates an n8n workflow against best practices
 * @param {Object} workflow - The workflow object to validate
 * @param {Object} options - Validation options
 * @param {string[]} options.validators - Array of validators to run ('naming', 'errorHandling', 'security', 'performance', 'documentation')
 * @param {string} options.strictness - Validation strictness level ('low', 'medium', 'high')
 * @returns {Object} Validation results for each category
 */
function validateWorkflow(workflow, options = {}) {
  const {
    validators = ['naming', 'errorHandling', 'security', 'performance', 'documentation'],
    strictness = 'medium'
  } = options;
  
  const results = {};
  let totalIssues = 0;
  
  if (validators.includes('naming')) {
    results.naming = validateNaming(workflow, strictness);
    totalIssues += results.naming.issues.length;
  }
  
  if (validators.includes('errorHandling')) {
    results.errorHandling = validateErrorHandling(workflow, strictness);
    totalIssues += results.errorHandling.issues.length;
  }
  
  if (validators.includes('security')) {
    results.security = validateSecurity(workflow, strictness);
    totalIssues += results.security.issues.length;
  }
  
  if (validators.includes('performance')) {
    results.performance = validatePerformance(workflow, strictness);
    totalIssues += results.performance.issues.length;
  }
  
  if (validators.includes('documentation')) {
    results.documentation = validateDocumentation(workflow, strictness);
    totalIssues += results.documentation.issues.length;
  }
  
  return {
    workflow: {
      id: workflow.id,
      name: workflow.name
    },
    passed: totalIssues === 0,
    totalIssues,
    strictness,
    results
  };
}

module.exports = {
  validateWorkflow,
  validateNaming,
  validateErrorHandling,
  validateSecurity,
  validatePerformance,
  validateDocumentation
};