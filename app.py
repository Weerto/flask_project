from flask import Flask, jsonify, request
from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

app = Flask(__name__)
load_dotenv()

uri = os.getenv("URI")
user = os.getenv("USERNAME")
password = os.getenv("PASSWORD")
driver = GraphDatabase.driver(uri, auth=(user, password), database="neo4j")

# no. 3 - get employees ########################
def get_employees(tx, filterOptions, sortOption, sortOrder):
    query = "MATCH (e:Employee)"

    if filterOptions:
        query += " WHERE "
        options = [f"e.{option}='{filterOptions[option]}'" for option in filterOptions]
        query += ", ".join(options)

    query += " RETURN e"

    if sortOption:
        query += f" ORDER BY e.{sortOption}"
        if sortOrder:
            query += " DESC" if sortOrder == "DESC" else ""

    results = tx.run(query).data()
    employees = [{'name': result['e']['name'], 'surname': result['e']['surname'], 'position': result['e']['position']} for result in results]
    return employees

@app.route('/employees', methods=(['GET']))
def get_employees_route():
    args = request.args
    filterOptions = {}
    sortOption = None
    sortOrder = None

    if len(args) > 0:
        filterArgs = ['name', 'surname', 'position']
        for option in filterArgs:
            if option in args.keys():
                filterOptions[option] = args[option]
    
        if 'sortBy' in args.keys():
            sortOption = args["sortBy"]
        if 'sortOrder' in args.keys():
            sortOrder = args["sortOrder"]
        

    with driver.session() as session:
        employees = session.read_transaction(get_employees, filterOptions, sortOption, sortOrder)
    response = {'employees': employees}
    return jsonify(response)

# no. 4 - add employees ########################
def add_employee(tx, name, surname, position, department):
    query = "MATCH (e:Employee {name: $name, surname: $surname}) RETURN e"
    result = tx.run(query, name=name, surname=surname).data()
    if result:
        return None

    relation = 'MANAGES' if position == 'Manager' else 'WORKS_IN'
    query = "MATCH (d:Department {name: $department}) CREATE (e:Employee {name: $name, surname: $surname, position: $position}) CREATE (e)-[:%s]->(d)" %relation
    tx.run(query, name=name, surname=surname, position=position, department=department, relation=relation)
    return "ok"

@app.route('/employees', methods=(['POST']))
def add_employee_route():
    data = request.get_json()
    for key in ['name', 'surname', 'position', 'department']:
        if key not in data:
            return jsonify({"message": f'Missing attribute: {key}'}), 400
    name = request.json["name"]
    surname = request.json["surname"]
    position = request.json["position"]
    department = request.json["department"]


    with driver.session() as session:
        employee = session.write_transaction(add_employee, name, surname, position, department)
    if not employee:
        return jsonify({"message": "Already exists"}), 409
    else:
        response = {'status': 'success'}
        return jsonify(response)

# no. 5 - update employees ########################
def update_employee(tx, id, new_values, new_department):
    query = "MATCH (e:Employee) WHERE ID(e)=$id RETURN e"
    result = tx.run(query, id=id).data()

    if not result:
        return None
    else:
        query = "MATCH (e:Employee) WHERE ID(e)=$id SET "
        set_clauses = [f"e.{key} = '{new_values[key]}'" for key in new_values]
        query += ', '.join(set_clauses)
        if not new_department:
            tx.run(query, id=id)
        else:
            if 'position' in new_values.keys():
                if new_values['position'] == 'Manager':
                    new_relation = 'MANAGES'
                else:
                    new_relation = "WORKS_IN"
            query += "WITH e MATCH (e)-[r]->(d:Department) DELETE r WITH e MATCH (nd:Department {name: $new_department}) CREATE (e)-[:%s]->(nd)"%new_relation
            tx.run(query, id=id, new_department=new_department)
        return new_values

@app.route("/employees/<int:id>", methods=['PUT'])
def update_employee_route(id):
    data = request.get_json()
    new_values = {}
    
    if 'department' in data.keys():
        new_department = data['department']
    else:
        new_department = None
    update_params = ['name', 'surname', 'position']
    for param in update_params:
        if param in data.keys():
            new_values[param] = data[param]
    
    with driver.session() as session:
        employee = session.write_transaction(update_employee, id, new_values, new_department)
    
    if not employee:
        return jsonify({"message":"Employee not found"}), 404
    else:
        return jsonify({"message": "success"})

# no. 6 - delete employees ########################
def delete_employee(tx, id):
    query = "MATCH (e:Employee) WHERE ID(e)=$id DETACH DELETE e"
    tx.run(query, id=id)

@app.route("/employees/<int:id>", methods=['DELETE'])
def delete_employee_route(id): 
    with driver.session() as session:
        query = "MATCH (e:Employee) WHERE ID(e)=$id RETURN e"
        employee = session.run(query, id=id).data()

        if employee:
            checkManagerQuery = "MATCH (e:Employee)-[:MANAGES]->(d:Department) WHERE ID(e)=$id RETURN e, d"
            result = session.run(checkManagerQuery, id=id).data()

            if result:
                departmentName = result[0]["d"]["name"]
                findNewManagerQuery = "MATCH (e:Employee)-[:WORKS_IN]->(d:Department {name: $departmentName}) RETURN e LIMIT 1"
                newManager = session.run(findNewManagerQuery, departmentName=departmentName).data()

                if newManager:
                    newManagerName = newManager[0]["e"]['name']
                    newManagerSurname = newManager[0]["e"]['surname']

                    assignNewManagerQuery = "MATCH (e:Employee {name: $newManagerName, surname: $newManagerSurname})-[r:WORKS_IN]->(d:Department {name: $departmentName}) SET e.position='Manager' CREATE (e)-[:MANAGES]->(d) DELETE r RETURN e, d"
                    session.run(assignNewManagerQuery, newManagerName=newManagerName, newManagerSurname=newManagerSurname, departmentName=departmentName)
                else:
                    removeDepartmentQuery = "MATCH (e:Employee)-[:MANAGES]->(d:Department) WHERE ID(e)=$id DETACH DELETE d"
                    session.run(removeDepartmentQuery, id=id)

                session.write_transaction(delete_employee, id)
            return jsonify({"message": "succes"})
        else:
            return jsonify({"message": "Employee not found"}), 404

# no. 7 - get subordinates
def is_manager(tx, id):
    query = "MATCH (e:Employee)-[:MANAGES]->(:Department) WHERE ID(e)=$id RETURN e"
    isManager = tx.run(query, id=id).data()
    return isManager

def get_subordinates(tx, id):
    query = "MATCH (m:Employee)-[:MANAGES]->(:Department)<-[:WORKS_IN]-(e:Employee) WHERE ID(m)=$id RETURN e"
    results = tx.run(query, id=id).data()
    employees = [{'name': result['e']['name'], 'surname': result['e']['name'], 'position': result['e']['position']} for result in results]
    return employees

@app.route("/employees/<int:id>/subordinates", methods=['GET'])
def get_subordinates_route(id):
    with driver.session() as session:
        isManager = session.read_transaction(is_manager, id)

        if not isManager:
            return jsonify({"message": "This employee is not a manager"}), 404
        else:
            employees = session.read_transaction(get_subordinates, id)
            response = jsonify({"message": employees})
            return response

# no. 8 - get department by employee
def get_department_info_by_employee(tx, id):
    query = "MATCH (e:Employee)-[:MANAGES|WORKS_IN]->(d:Department) WHERE ID(e)=$id MATCH (r:Employee)-[:WORKS_IN|MANAGES]->(d) MATCH (m:Employee)-[:MANAGES]->(d) RETURN d.name as name, count(r) AS employees, m.name + ' ' + m.surname as manager"
    results = tx.run(query, id=id).data()
    return results

@app.route("/employees/<int:id>/department", methods=['GET'])
def get_department_info_by_employee_route(id):
    with driver.session() as session:
        department = session.read_transaction(get_department_info_by_employee, id)
        if not department:
            return jsonify({"department": "Employee not found"}), 404
        else:
            response = jsonify({"message": department})
            return response
        
# no. 9 - get departments ########################
def get_departments(tx, filterOptions, sortOption, sortOrder):
    query = "MATCH (d:Department)<-[:MANAGES|WORKS_IN]-(e:Employee) WITH count(e) as size, d"

    if filterOptions:
        query += " WHERE "
        options = [f"d.{option}='{filterOptions[option]}'" if option=="name" else f"size={filterOptions[option]}" for option in filterOptions]
        query += ", ".join(options)

    query += " RETURN d.name as name, size"

    if sortOption:
        query += f" ORDER BY {sortOption}"
        if sortOrder:
            query += " DESC" if sortOrder == "DESC" else ""
    results = tx.run(query).data()
    depts = [{'name': result['name'], 'size': result['size']} for result in results]
    return depts

@app.route('/departments', methods=(['GET']))
def get_departments_route():
    args = request.args
    filterOptions = {}
    sortOption = None
    sortOrder = None

    if len(args) > 0:
        filterArgs = ['name', 'size']
        for option in filterArgs:
            if option in args.keys():
                filterOptions[option] = args[option]
    
        if 'sortBy' in args.keys():
            sortOption = args["sortBy"]
        if 'sortOrder' in args.keys():
            sortOrder = args["sortOrder"]
        

    with driver.session() as session:
        departments = session.read_transaction(get_departments, filterOptions, sortOption, sortOrder)
    response = {'departments': departments}
    return jsonify(response)

# employees from department
def get_employees_from_department(tx, id):
    query = "MATCH (e:Employee)-[:MANAGES|WORKS_IN]->(d:Department) WHERE ID(d)=$id RETURN e"
    results = tx.run(query, id=id).data()
    employees = [{'name': result['e']['name'], 'surname': result['e']['name'], 'position': result['e']['position']} for result in results]
    return employees

@app.route("/departments/<int:id>/employees", methods=['GET'])
def get_employees_from_department_route(id):
    with driver.session() as session:
        existsDepartment = session.read_transaction(get_employees_from_department, id)
        if not existsDepartment:
            response = jsonify({"message": 'Department not found'}), 404
            return response
        else:
            response = jsonify({"employees": existsDepartment})
            return response

        # employees = session.read_transaction(get_employees_from_department, id)


        
if __name__ == '__main__':
    app.run()